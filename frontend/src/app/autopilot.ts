// ИИ-помощник диспетчера поверх планировщика Н. Режимы: «Советы» (по умолчанию) — сам следит за станцией,
// при конфликте или сбое считает варианты и рекомендует лучший, принимает человек; «Автопилот» — принимает сам.
// Он не строит расписание сам и не обходит проверки сервера: просит пересчёт, выбирает вариант по явным правилам
// и принимает его через тот же POST /api/plans/{id}/apply, что и человек. Сервер может отказать (409) — тогда повтор.
// Работает в браузере диспетчера, пока вкладка открыта (серверный автопилот — вопрос к И, см. docs/decisions.md).
import { api, HttpError } from '../api/client'
import { currentDelays } from '../api/adapt'
import type { Plan, Snapshot } from '../api/types'
import { fmtDur, fmtT, STRATEGY } from './labels'
import { useStore } from './store'
import { computeTips, pickBest } from './autopilotPolicy'

export { pickBest }

const REPLAN_COOLDOWN_MS = 4000
const MAX_RETRIES = 3

/** Конфликты, на которые стоит реагировать: нарушения плана сразу, ожидания при исполнении — если длятся > 30 с модели. */
const actionable = (s: Snapshot) => s.conflicts.filter((c) => c.kind === 'plan' || s.sim_time_s - c.start_s >= 30)
const conflictSig = (s: Snapshot) => actionable(s).map((c) => c.id).sort().join('|')
const curDelay = (s: Snapshot) => currentDelays(s).total

export function startAutopilot(): () => void {
  let handledJob: string | null = null
  let handledSig = ''
  let handledEpoch = -2
  let lastReplanAt = 0
  let retries = 0
  let busy = false
  let epochSeenAt = 0
  let epochSeen = -2
  let ownApply = false
  let tipsVersion = -1
  let lastPlanId: string | null = null
  let tipsAt = -999

  const st = () => useStore.getState()

  const requestReplan = async (why: string) => {
    lastReplanAt = Date.now()
    st().apUpdate({ status: 'replanning', note: why })
    st().apLog({ kind: 'replan', title: 'Запросил пересчёт плана', reasons: [why] })
    await st().requestReplan()
  }

  const decide = async (plans: Plan[], snap: Snapshot) => {
    busy = true
    st().apUpdate({ status: 'deciding', note: 'Сравниваю варианты' })
    const best = pickBest(plans)
    const nConf = snap.conflicts.length
    if (!best) {
      const why = plans.map((p) => `${STRATEGY[p.strategy] ?? p.strategy}: ${p.status === 'partial' ? 'не все поезда размещены' : p.status}`)
      st().apLog({ kind: 'skip', title: 'Не принял ни один вариант', reasons: [...why, 'Безопасные операции продолжаются, заблокированные ждут. Повторю после освобождения ресурсов.'] })
      st().apUpdate({ status: 'watching', note: 'Нет допустимого полного плана' })
      busy = false
      return
    }
    if ((best.metrics.changed_count ?? 1) === 0 && nConf === 0) {
      // в автопилоте варианты убираем; в режиме советов оставляем — диспетчер может сам их сравнить
      if (st().autopilot.mode === 'auto') useStore.setState((x) => ({ replan: { ...x.replan, status: 'idle', plans: [], job_id: null } }))
      if (st().autopilot.log[0]?.kind !== 'keep')
        st().apLog({ kind: 'keep', title: 'Оставил текущий план', reasons: ['Лучший вариант не меняет будущих назначений, конфликтов нет'], plan_id: best.id })
      st().apUpdate({ status: 'watching', note: 'Текущий план оптимален' })
      busy = false
      return
    }
    const reasons: string[] = []
    const other = plans.find((p) => p.id !== best.id)
    reasons.push(`Выбран «${STRATEGY[best.strategy] ?? best.strategy}»${other ? ` вместо «${STRATEGY[other.strategy] ?? other.strategy}»` : ''}: ` +
      `задержка ${fmtDur(best.metrics.total_delay_s)}${other ? ` против ${fmtDur(other.metrics.total_delay_s)}` : ''}, переназначений ${best.metrics.changed_count ?? '—'}`)
    if (nConf) reasons.push(`Снимает ${nConf} ${nConf === 1 ? 'конфликт' : nConf < 5 ? 'конфликта' : 'конфликтов'} текущего плана`)
    const was = curDelay(snap)
    if (best.metrics.total_delay_s !== null) {
      const d = best.metrics.total_delay_s - was
      if (d !== 0) reasons.push(`Прогноз суммарной задержки: ${fmtDur(was)} → ${fmtDur(best.metrics.total_delay_s)}${d < 0 ? ` (лучше на ${fmtDur(-d)})` : d > 0 ? ` (хуже на ${fmtDur(d)}, но без нарушений)` : ''}`)
    }
    if (best.index_forecast) reasons.push(`Индекс эффективности (прогноз 15 мин): ${best.index_forecast.value}`)
    for (const e of best.explanations.slice(0, 3)) reasons.push(e.message)

    if (st().autopilot.mode === 'advise') {
      const title = `Рекомендую «${STRATEGY[best.strategy] ?? best.strategy}»`
      useStore.setState((x) => ({ autopilot: { ...x.autopilot, advice: { plan_id: best.id, job_id: x.replan.job_id ?? '', epoch: snap.epoch, title, reasons, at_sim: snap.sim_time_s } } }))
      st().apLog({ kind: 'advice', title, reasons, plan_id: best.id })
      st().toast('info', `ИИ-помощник: ${title.toLowerCase()} — ${reasons[1] ?? reasons[0]}`)
      st().apUpdate({ status: 'watching', note: 'Есть рекомендация — примите или отклоните' })
      busy = false
      return
    }
    st().apUpdate({ status: 'applying', note: `Принимаю ${best.id}` })
    try {
      await api.apply(best.id, snap.run_id, snap.state_version)
      retries = 0
      ownApply = true // следующий рост epoch — это наше же принятие плана, а не новый сбой
      useStore.setState((s) => ({
        autopilot: { ...s.autopilot, applied: s.autopilot.applied + 1, advice: null },
        replan: { ...s.replan, status: 'idle', plans: [], job_id: null },
        compareOpen: false, previewPlanId: null,
      }))
      st().apLog({ kind: 'apply', title: `Принял план ${best.id}`, reasons, plan_id: best.id })
      st().toast('ok', `ИИ-диспетчер принял план: ${STRATEGY[best.strategy] ?? best.strategy}`)
      st().apUpdate({ status: 'watching', note: `Последнее решение в ${fmtT(snap.sim_time_s)}` })
    } catch (e) {
      const code = e instanceof HttpError ? e.body.code : 'NETWORK'
      const msg = e instanceof HttpError ? e.body.message : 'Сервер не отвечает'
      st().apLog({ kind: 'error', title: 'Сервер отклонил план', reasons: [`${code}: ${msg}`] })
      if (e instanceof HttpError && e.status === 409 && retries < MAX_RETRIES) {
        retries++
        busy = false
        await requestReplan(`Повтор ${retries}/${MAX_RETRIES}: обстановка изменилась, пока шёл расчёт`)
        return
      }
      st().apUpdate({ status: 'watching', note: 'Жду изменения обстановки' })
    }
    busy = false
  }

  const tick = () => {
    const s = st()
    const snap = s.snapshot
    if (s.autopilot.mode === 'off' || !snap) return
    // советы-наблюдения обновляются всегда, когда помощник включён
    if (snap.state_version !== tipsVersion || snap.sim_time_s - tipsAt >= 10) {
      tipsVersion = snap.state_version
      tipsAt = snap.sim_time_s
      const tips = computeTips(s.history?.snapshot ?? snap)
      if (JSON.stringify(tips) !== JSON.stringify(s.autopilot.tips)) s.apUpdate({ tips })
    }
    // рекомендация устарела — обстановка изменилась после её расчёта
    if (s.autopilot.advice && s.autopilot.advice.epoch !== snap.epoch) {
      s.apUpdate({ advice: null })
    }
    const blocked = s.conn !== 'online' || !!s.history || s.user?.role === 'viewer'
    if (blocked) {
      if (s.autopilot.status !== 'paused')
        s.apUpdate({ status: 'paused', note: s.history ? 'Вы в режиме истории' : s.conn !== 'online' ? 'Нет связи с сервером' : 'Наблюдатель: только советы, без пересчёта' })
      return
    }
    if (s.autopilot.status === 'paused') s.apUpdate({ status: 'watching', note: s.autopilot.mode === 'auto' ? 'Сам принимаю лучший допустимый план' : 'Слежу за станцией и даю советы' })
    if (busy) return
    // 1) готовы варианты — принять решение
    if (s.replan.status === 'done' && s.replan.job_id && s.replan.job_id !== handledJob) {
      handledJob = s.replan.job_id
      handledSig = conflictSig(snap)
      handledEpoch = snap.epoch
      void decide(s.replan.plans, snap)
      return
    }
    if (s.replan.status !== 'running' && (s.autopilot.status === 'replanning' || s.autopilot.status === 'deciding')) {
      s.apUpdate({ status: 'watching', note: s.autopilot.advice ? 'Есть рекомендация — примите или отклоните' : s.autopilot.mode === 'auto' ? 'Сам принимаю лучший допустимый план' : 'Слежу за станцией и даю советы' })
    }
    if (s.replan.status === 'running') {
      if (s.autopilot.status !== 'replanning') s.apUpdate({ status: 'replanning', note: 'Планировщик считает варианты' })
      return
    }
    // 2) новые конфликты или сбой — запросить пересчёт (с паузой, чтобы не засыпать сервер)
    const sig = conflictSig(snap)
    const act = actionable(snap)
    const newConflicts = act.length > 0 && sig !== handledSig
    if (snap.epoch !== epochSeen) {
      epochSeen = snap.epoch
      epochSeenAt = Date.now()
      // принятие плана (нами или человеком) тоже меняет epoch — это не новый сбой
      if (ownApply || snap.active_plan_id !== lastPlanId) {
        ownApply = false
        handledEpoch = snap.epoch
      }
      lastPlanId = snap.active_plan_id
    }
    // после сбоя сервер сам запускает пересчёт — ждём его 3 с, чтобы не заказывать второй
    const newIncident = snap.epoch !== -1 && handledEpoch !== -2 && snap.epoch !== handledEpoch && s.replan.status !== 'done' &&
      Date.now() - epochSeenAt > 3000
    const settled = Date.now() - epochSeenAt > 3000 // сразу после сбоя сервер сам считает варианты
    if (((newConflicts && settled) || newIncident) && Date.now() - lastReplanAt > REPLAN_COOLDOWN_MS) {
      handledSig = sig
      handledEpoch = snap.epoch
      const why = newConflicts
        ? `Конфликтов: ${act.length}. ${act.slice(0, 2).map((c) => c.message).join('; ')}`
        : 'Обстановка изменилась после сбоя'
      void requestReplan(why)
    }
  }

  const unsub = useStore.subscribe((s, prev) => {
    if (s.autopilot.mode !== prev.autopilot.mode && s.autopilot.mode !== 'off') {
      // при включении учитываем уже готовые варианты и текущие конфликты
      handledJob = null
      handledSig = ''
      handledEpoch = s.snapshot?.epoch ?? -2
      retries = 0
    }
    tick()
  })
  const timer = window.setInterval(tick, 1000)
  return () => {
    unsub()
    window.clearInterval(timer)
  }
}
