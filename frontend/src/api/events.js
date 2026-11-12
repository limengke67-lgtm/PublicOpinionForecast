import service from './index'

const root = '/api/research'
export const listEvents = () => service.get(`${root}/events`)
export const createEvent = data => service.post(`${root}/events`, data)
export const createEventDemo = () => service.post(`${root}/events/demo`)
export const getEvent = id => service.get(`${root}/events/${id}`)
export const freezeEvent = (id, data) => service.post(`${root}/events/${id}/freeze`, data)
export const ingestEvent = (id, data) => service.post(`${root}/events/${id}/observations`, data)
export const replayEventStep = (id, data) => service.post(`${root}/events/${id}/replay-step`, data)
export const refreshEventCase = (id, data) => service.post(`${root}/events/${id}/refresh-case`, data)
export const createEventReport = (id, data) => service.post(`${root}/events/${id}/report`, data)
export const getEvidence = id => service.get(`${root}/evidence/${id}`)
export const exportEvidence = (id, format) => service.get(`${root}/evidence/${id}/export`, { params: { format }, responseType: 'blob' })
