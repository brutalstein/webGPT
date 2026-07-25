export async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-Requested-With': 'OS-Web',
      ...(options.headers || {}),
    },
    ...options,
  });
  if (response.status === 204) return null;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || `İstek başarısız: ${response.status}`);
  }
  return body;
}

export function createSocket(onEvent, onStatus) {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  let socket;
  let closed = false;
  let reconnectTimer;
  let attempts = 0;
  const pending = new Map();

  const connect = () => {
    onStatus?.('connecting');
    socket = new WebSocket(`${protocol}//${location.host}/api/ws`);
    socket.addEventListener('open', () => {
      attempts = 0;
      onStatus?.('connected');
    });
    socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if ((message.type === 'command.result' || message.type === 'command.error') && message.request_id) {
        const waiter = pending.get(message.request_id);
        if (waiter) {
          pending.delete(message.request_id);
          window.clearTimeout(waiter.timeoutId);
          if (message.type === 'command.error') waiter.reject(new Error(message.payload?.error || 'Komut başarısız'));
          else waiter.resolve(message.payload);
        }
      }
      onEvent(message);
    });
    socket.addEventListener('close', () => {
      onStatus?.('disconnected');
      for (const waiter of pending.values()) {
        window.clearTimeout(waiter.timeoutId);
        waiter.reject(new Error('WebSocket bağlantısı kesildi.'));
      }
      pending.clear();
      if (!closed) {
        const delay = Math.min(5000, 400 * 2 ** attempts++);
        reconnectTimer = window.setTimeout(connect, delay);
      }
    });
    socket.addEventListener('error', () => socket.close());
  };

  connect();

  return {
    send(type, payload = {}) {
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        return Promise.reject(new Error('WebSocket bağlı değil.'));
      }
      const requestId = crypto.randomUUID();
      return new Promise((resolve, reject) => {
        const timeoutId = window.setTimeout(() => {
          if (pending.delete(requestId)) reject(new Error('Komut zaman aşımına uğradı.'));
        }, 15 * 60 * 1000);
        pending.set(requestId, { resolve, reject, timeoutId });
        try {
          socket.send(JSON.stringify({ type, request_id: requestId, ...payload }));
        } catch (error) {
          pending.delete(requestId);
          window.clearTimeout(timeoutId);
          reject(error);
        }
      });
    },
    close() {
      closed = true;
      window.clearTimeout(reconnectTimer);
      socket?.close();
      for (const waiter of pending.values()) { window.clearTimeout(waiter.timeoutId); waiter.reject(new Error('Bağlantı kapandı.')); }
      pending.clear();
    },
  };
}
