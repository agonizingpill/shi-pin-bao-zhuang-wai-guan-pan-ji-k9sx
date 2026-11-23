const key = 'hedi.packaging.session';
export const session = {
  get() {
    try { return JSON.parse(sessionStorage.getItem(key)); }
    catch { return null; }
  },
  save(account) { sessionStorage.setItem(key, JSON.stringify(account)); },
  clear() { sessionStorage.removeItem(key); },
};

async function request(path, data, method = 'GET') {
  const token = session.get()?.access_token;
  const response = await fetch(`/api${path}`, {
    method,
    headers: {
      ...(token ? {Authorization:`Bearer ${token}`} : {}),
      ...(data ? {'Content-Type':'application/json'} : {}),
    },
    body: data ? JSON.stringify(data) : undefined,
  });
  const result = await response.json();
  if (response.status === 401 && path !== '/login') {
    session.clear();
    window.dispatchEvent(new Event('session-expired'));
  }
  if (!response.ok) {
    const detail = result.detail;
    const message = Array.isArray(detail) ? detail.map(item => `${item.loc.at(-1)} · ${item.msg}`).join('；') : detail?.message || detail;
    const channels = [...(detail?.missing || []), ...(detail?.exceeds || [])];
    const error = new Error(message || `请求失败 (${response.status})`);
    error.channels = channels;
    throw error;
  }
  return result;
}

export const api = {
  login: payload => request('/login', payload, 'POST'),
  lots: () => request('/lots'),
  materials: () => request('/materials'),
  detail: id => request(`/lots/${id}`),
  register: payload => request('/lots', payload, 'POST'),
  action: (id, endpoint, payload) => request(`/lots/${id}/${endpoint}`, payload, 'POST'),
  calibrations: () => request('/calibrations'),
  calibration: id => request(`/calibrations/${id}`),
  registerCalibration: payload => request('/calibrations', payload, 'POST'),
  amendCalibration: (id, payload) => request(`/calibrations/${id}/amend`, payload, 'POST'),
  retryCalibration: id => request(`/calibrations/${id}/retry`, null, 'POST'),
  clearCalibrationTask: (id, taskId, payload) => request(`/calibrations/${id}/tasks/${taskId}/clear`, payload, 'POST'),
};
