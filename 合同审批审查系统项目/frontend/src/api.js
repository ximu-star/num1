export const CONTRACT_SERVICE_URL =
  import.meta.env.VITE_CONTRACT_SERVICE_URL || 'http://127.0.0.1:8001'

async function request(path, options = {}) {
  const response = await fetch(`${CONTRACT_SERVICE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  })
  const body = await response.json().catch(() => ({ message: response.statusText }))
  if (!response.ok) {
    throw new Error(body.detail || body.message || response.statusText)
  }
  if (body.success === false) {
    throw new Error(body.message || '请求失败')
  }
  return body.data !== undefined ? body.data : body
}

export const api = {
  summary: () => request('/api/v1/admin/summary'),
  tasks: (status = '') =>
    request(`/api/v1/admin/tasks${status ? `?status=${encodeURIComponent(status)}` : ''}`),
  task: (id) => request(`/api/v1/admin/tasks/${id}`),
  pull: () => request('/api/v1/admin/pull', { method: 'POST' }),
  retry: (id) => request(`/api/v1/admin/tasks/${id}/retry`, { method: 'POST' }),
  review: (id, force = false) =>
    request(`/api/v1/admin/tasks/${id}/review?force=${force}`, { method: 'POST' }),
  setStandard: (id, value) =>
    request(`/api/v1/admin/tasks/${id}/standard-version?is_standard=${value}`, {
      method: 'PATCH',
    }),
  rules: () => request('/api/v1/admin/rules'),
  createRule: (rule) =>
    request('/api/v1/admin/rules', { method: 'POST', body: JSON.stringify(rule) }),
  updateRule: (id, rule) =>
    request(`/api/v1/admin/rules/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(rule),
    }),
  logs: (taskId = '') =>
    request(`/api/v1/admin/logs?limit=300${taskId ? `&task_id=${taskId}` : ''}`),
}
