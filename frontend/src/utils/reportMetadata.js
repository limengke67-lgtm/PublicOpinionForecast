/** Load a report while its background worker is creating the initial metadata. */
export async function loadReportWhenReady(loadReport, reportId, isCurrent, pause = ms => new Promise(resolve => setTimeout(resolve, ms))) {
  for (let attempt = 0; attempt < 10; attempt++) {
    try {
      return await loadReport(reportId)
    } catch (error) {
      if (error.response?.status !== 404 || attempt === 9) throw error
      if (!isCurrent()) return null
      await pause(500)
      if (!isCurrent()) return null
    }
  }
}
