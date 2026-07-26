const DEFAULT_REQUEST_TIMEOUT_MS = 45_000;

export async function api(path, options = {}) {
  const {
    headers: optionHeaders = {},
    timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
    signal: callerSignal,
    ...requestOptions
  } = options;
  const headers = {
    Accept: 'application/json',
    'X-Requested-With': 'OS-Web',
    ...optionHeaders,
  };
  if (requestOptions.body != null && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }

  const controller = new AbortController();
  let timeoutId = 0;
  let timedOut = false;
  const abortFromCaller = () => controller.abort();
  if (callerSignal?.aborted) abortFromCaller();
  else callerSignal?.addEventListener('abort', abortFromCaller, { once: true });
  if (Number(timeoutMs) > 0) {
    timeoutId = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, Number(timeoutMs));
  }

  let response;
  try {
    response = await fetch(path, {
      credentials: 'same-origin',
      ...requestOptions,
      headers,
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted || error.name === 'AbortError') {
      throw new Error(timedOut ? 'Yerel sunucu isteği zaman aşımına uğradı.' : 'Yerel sunucu isteği iptal edildi.');
    }
    throw new Error(`Yerel sunucuya ulaşılamadı: ${error.message}`);
  } finally {
    window.clearTimeout(timeoutId);
    callerSignal?.removeEventListener('abort', abortFromCaller);
  }

  if (response.status === 204) return null;
  const raw = await response.text();
  let body = {};
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      body = { detail: raw.slice(0, 500) };
    }
  }
  if (!response.ok) {
    if (response.status === 401) {
      throw new Error('Yerel web oturumu sona erdi. Sayfayı terminalden yeniden aç.');
    }
    throw new Error(body.detail || `İstek başarısız: ${response.status}`);
  }
  return body;
}

export function createSocket(onEvent, onStatus) {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  let socket = null;
  let closed = false;
  let reconnectTimer = 0;
  let heartbeatTimer = 0;
  let attempts = 0;
  let generation = 0;
  let lastPongAt = Date.now();
  const pending = new Map();

  const setStatus = (status) => onStatus?.(status);

  const rejectPending = (message) => {
    for (const waiter of pending.values()) {
      window.clearTimeout(waiter.timeoutId);
      waiter.reject(new Error(message));
    }
    pending.clear();
  };

  const stopHeartbeat = () => {
    window.clearInterval(heartbeatTimer);
    heartbeatTimer = 0;
  };

  const startHeartbeat = () => {
    stopHeartbeat();
    lastPongAt = Date.now();
    heartbeatTimer = window.setInterval(() => {
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      if (Date.now() - lastPongAt > 55_000) {
        socket.close(4000, 'heartbeat timeout');
        return;
      }
      try {
        socket.send(JSON.stringify({ type: 'ping' }));
      } catch {
        socket.close();
      }
    }, 20_000);
  };

  const scheduleReconnect = () => {
    if (closed) return;
    if (navigator.onLine === false) {
      setStatus('offline');
      return;
    }
    const exponential = Math.min(10_000, 450 * (2 ** Math.min(attempts, 5)));
    const delay = Math.round(exponential * (0.8 + Math.random() * 0.4));
    attempts += 1;
    setStatus('reconnecting');
    window.clearTimeout(reconnectTimer);
    reconnectTimer = window.setTimeout(connect, delay);
  };

  const connect = () => {
    if (closed) return;
    if (navigator.onLine === false) {
      setStatus('offline');
      return;
    }
    if (socket && [WebSocket.CONNECTING, WebSocket.OPEN].includes(socket.readyState)) return;

    const currentGeneration = ++generation;
    setStatus(attempts > 0 ? 'reconnecting' : 'connecting');
    socket = new WebSocket(`${protocol}//${location.host}/api/ws`);

    socket.addEventListener('open', () => {
      if (currentGeneration !== generation) return;
      attempts = 0;
      setStatus('connected');
      startHeartbeat();
    });

    socket.addEventListener('message', (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        onEvent({
          type: 'socket.protocol_error',
          payload: { error: 'Sunucudan geçersiz WebSocket verisi geldi.' },
        });
        return;
      }
      if (message.type === 'pong') {
        lastPongAt = Date.now();
        return;
      }
      if ((message.type === 'command.result' || message.type === 'command.error') && message.request_id) {
        const waiter = pending.get(message.request_id);
        if (waiter) {
          pending.delete(message.request_id);
          window.clearTimeout(waiter.timeoutId);
          if (message.type === 'command.error') {
            waiter.reject(new Error(message.payload?.error || 'Komut başarısız'));
          } else {
            waiter.resolve(message.payload);
          }
        }
      }
      onEvent(message);
    });

    socket.addEventListener('close', () => {
      if (currentGeneration !== generation) return;
      stopHeartbeat();
      socket = null;
      rejectPending('WebSocket bağlantısı kesildi; işlem durumu yeniden bağlanınca eşitlenecek.');
      if (!closed) scheduleReconnect();
    });

    socket.addEventListener('error', () => socket?.close());
  };

  const handleOnline = () => {
    attempts = 0;
    connect();
  };
  const handleOffline = () => {
    setStatus('offline');
    socket?.close();
  };
  const handleVisibility = () => {
    if (
      document.visibilityState === 'visible'
      && (!socket || [WebSocket.CLOSING, WebSocket.CLOSED].includes(socket.readyState))
    ) {
      connect();
    }
  };

  window.addEventListener('online', handleOnline);
  window.addEventListener('offline', handleOffline);
  document.addEventListener('visibilitychange', handleVisibility);
  connect();

  return {
    send(type, payload = {}) {
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        return Promise.reject(new Error('WebSocket bağlı değil. Yeniden bağlantıyı bekle.'));
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
    reconnect() {
      attempts = 0;
      generation += 1;
      window.clearTimeout(reconnectTimer);
      stopHeartbeat();
      const previous = socket;
      socket = null;
      previous?.close(4001, 'manual reconnect');
      connect();
    },
    close() {
      closed = true;
      generation += 1;
      window.clearTimeout(reconnectTimer);
      stopHeartbeat();
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
      document.removeEventListener('visibilitychange', handleVisibility);
      socket?.close(1000, 'page closed');
      socket = null;
      rejectPending('Bağlantı kapandı.');
      setStatus('disconnected');
    },
  };
}
