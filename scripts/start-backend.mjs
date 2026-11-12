import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const backend = path.join(root, 'backend')
const python = path.join(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const env = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' }
const researchOnly = process.argv.includes('--research-only')
if (researchOnly) env.MIROFISH_RESEARCH_ONLY = '1'
const pythonArgs = ['-X', 'utf8', 'run.py', ...(researchOnly ? ['--research-only'] : []), ...(process.argv.includes('--serve-frontend') ? ['--serve-frontend'] : [])]
const child = existsSync(python)
  ? spawn(python, pythonArgs, { cwd: backend, stdio: 'inherit', env })
  : spawn('uv', ['run', 'python', ...pythonArgs], { cwd: backend, stdio: 'inherit', env })
child.on('error', error => { console.error(`后端启动失败：${error.message}。请先安装项目独立环境。`); process.exitCode = 1 })
child.on('exit', code => { process.exitCode = code ?? 1 })
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal))
