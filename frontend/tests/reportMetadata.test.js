import test from 'node:test'
import assert from 'node:assert/strict'
import { loadReportWhenReady } from '../src/utils/reportMetadata.js'

test('retries briefly while the background worker creates report metadata', async () => {
  let attempts = 0
  const value = await loadReportWhenReady(async id => {
    assert.equal(id, 'report_example')
    if (++attempts < 3) throw { response: { status: 404 } }
    return { success: true }
  }, 'report_example', () => true, async () => {})
  assert.equal(attempts, 3)
  assert.equal(value.success, true)
})

test('does not retry unrelated errors or an abandoned report', async () => {
  let attempts = 0
  await assert.rejects(() => loadReportWhenReady(async () => {
    attempts++
    throw new Error('network failed')
  }, 'report_example', () => true, async () => {}), /network failed/)
  assert.equal(attempts, 1)

  attempts = 0
  const value = await loadReportWhenReady(async () => {
    attempts++
    throw { response: { status: 404 } }
  }, 'report_example', () => false, async () => {})
  assert.equal(value, null)
  assert.equal(attempts, 1)
})
