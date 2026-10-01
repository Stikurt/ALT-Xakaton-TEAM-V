// Сборка страницы-демо: VITE_MOCK=1, всё в один HTML (CSS и JS встроены). Результат: dist-demo/uzel12-demo.html
import { execSync } from 'node:child_process'
import { readFileSync, writeFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

execSync('npx vite build --outDir dist-demo --emptyOutDir', { stdio: 'inherit', env: { ...process.env, VITE_MOCK: '1' } })
const dir = 'dist-demo'
let html = readFileSync(join(dir, 'index.html'), 'utf8')
const assets = join(dir, 'assets')
for (const f of readdirSync(assets)) {
  const body = readFileSync(join(assets, f), 'utf8')
  if (f.endsWith('.css')) {
    html = html.replace(new RegExp(`<link[^>]*href="/assets/${f}"[^>]*>`), () => `<style>${body}</style>`)
  } else if (f.endsWith('.js')) {
    const safe = body.replace(/<\/script/gi, '<\\/script')
    html = html.replace(new RegExp(`<script[^>]*src="/assets/${f}"[^>]*></script>`), () => `<script type="module">${safe}</script>`)
  }
}
writeFileSync(join(dir, 'uzel12-demo.html'), html)
// фрагмент для публикации: без doctype/html/head/body (оболочку добавляет площадка)
const head = html.match(/<head>([\s\S]*?)<\/head>/)[1].replace(/<meta charset[^>]*>|<meta name="viewport"[^>]*>/g, '')
const bodyInner = html.match(/<body>([\s\S]*?)<\/body>/)[1]
writeFileSync(join(dir, 'uzel12-artifact.html'), head.trim() + '\n' + bodyInner.trim() + '\n')
console.log('demo:', (html.length / 1024).toFixed(0), 'KB')
