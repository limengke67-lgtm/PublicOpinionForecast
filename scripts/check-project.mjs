import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const backend = path.join(root, 'backend')
const python = path.join(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
if (!existsSync(python)) {
  console.error('未找到项目独立 Python 环境，请先按文档安装后端依赖。')
  process.exit(1)
}
const repositoryTests = process.argv.includes('--test-repository')
const args = process.argv.includes('--test') || repositoryTests
  ? ['-X', 'utf8', '-m', 'pytest', 'tests', '-q']
  : process.argv.includes('--backup')
    ? ['-X', 'utf8', 'scripts/backup_project.py', ...process.argv.slice(3)]
    : ['-X', 'utf8', 'scripts/doctor.py', ...process.argv.slice(2)]
const result = spawnSync(python, args, { cwd: repositoryTests ? root : backend, stdio: 'inherit', env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } })
if (result.error) console.error(result.error.message)
process.exitCode = result.status ?? 1
