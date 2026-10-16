(() => {
  const $ = (id) => document.getElementById(id);
  const form = $('orderForm');
  const input = $('orderInput');
  const messageList = $('messageList');
  const welcomeBlock = $('welcomeBlock');
  const typingRow = $('typingRow');
  const sendBtn = $('sendBtn');
  const connectionDot = $('connectionDot');
  const connectionText = $('connectionText');
  const headerStatus = $('headerStatus');
  const toastStack = $('toastStack');
  const voiceBtn = $('voiceBtn');
  const voiceBtnText = $('voiceBtnText');
  const voiceBannerText = $('voiceBannerText');

  let recognition = null;
  let listening = false;
  let lastServerState = null;
  let activeController = null;   // hay una orden en curso desde esta página
  let stopping = false;

  // ---- Estilos de las tarjetas de resultado (inyectados desde aquí) ----
  (function injectStyles() {
    const style = document.createElement('style');
    style.textContent = `
      .bubble.rich { max-width: 100%; }
      .audit-summary { margin: 0 0 12px; line-height: 1.5; }
      .audit-card { border: 1px solid rgba(148,163,184,.25); border-left-width: 4px; border-radius: 12px;
        padding: 12px 14px; margin: 0 0 10px; background: rgba(148,163,184,.07); }
      .audit-card.alert { border-left-color: #f59e0b; }
      .audit-card.ok { border-left-color: #22c55e; }
      .audit-card.unk { border-left-color: #94a3b8; }
      .audit-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 6px; }
      .audit-client { opacity: .7; font-size: .9em; }
      .audit-badge { margin-left: auto; font-size: .75em; font-weight: 700; padding: 2px 10px;
        border-radius: 999px; background: rgba(148,163,184,.2); }
      .audit-card.alert .audit-badge { background: rgba(245,158,11,.2); color: #fbbf24; }
      .audit-card.ok .audit-badge { background: rgba(34,197,94,.18); color: #4ade80; }
      .audit-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
      .audit-chip { font-size: .75em; padding: 2px 8px; border-radius: 6px; background: rgba(148,163,184,.18); }
      .audit-cause { margin: 0 0 8px; line-height: 1.5; }
      .audit-metrics { display: flex; flex-wrap: wrap; gap: 14px; font-size: .85em; opacity: .85; margin-bottom: 6px; }
      .audit-card details { font-size: .85em; opacity: .85; }
      .audit-card summary { cursor: pointer; }
      .audit-evidence { margin: 6px 0 0; line-height: 1.5; white-space: pre-wrap; }
      .audit-total { margin-top: 4px; font-size: .9em; opacity: .9; }
      .message-edited { opacity: .6; font-size: .8em; font-style: normal; }
    `;
    document.head.appendChild(style);
  })();

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function money(n) {
    const value = Number(n) || 0;
    return `$${value.toLocaleString('es-MX', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} MXN`;
  }

  function renderResultado(bubble, r) {
    bubble.appendChild(el('p', 'audit-summary', r.resumen));

    (r.ordenes || []).forEach((o) => {
      const tipo = o.accion === 'alertar' ? 'alert' : o.accion === 'ignorar' ? 'ok' : 'unk';
      const badge = o.accion === 'alertar' ? 'Alerta' : o.accion === 'ignorar' ? 'Sin acción' : 'Revisar';
      const card = el('div', `audit-card ${tipo}`);

      const head = el('div', 'audit-head');
      head.append(
        el('strong', '', `Orden #${o.orden_id}`),
        el('span', 'audit-client', o.cliente || ''),
        el('span', 'audit-badge', badge)
      );
      card.appendChild(head);

      const chips = el('div', 'audit-chips');
      (o.banderas || []).forEach((b) => chips.appendChild(el('span', 'audit-chip', b)));
      card.appendChild(chips);

      if (o.causa) card.appendChild(el('p', 'audit-cause', o.causa));

      const metrics = el('div', 'audit-metrics');
      if (o.fecha) metrics.appendChild(el('span', '', `Fecha: ${o.fecha}`));
      if (typeof o.margen_pct === 'number') metrics.appendChild(el('span', '', `Margen: ${o.margen_pct}%`));
      if (Number(o.perdida_mxn) > 0) metrics.appendChild(el('span', '', `Pérdida estimada: ${money(o.perdida_mxn)}`));
      card.appendChild(metrics);

      if (o.evidencia) {
        const details = el('details');
        details.append(el('summary', '', 'Ver evidencia'), el('p', 'audit-evidence', o.evidencia));
        card.appendChild(details);
      }
      bubble.appendChild(card);
    });

    if (Number(r.perdida_total_mxn) > 0) {
      bubble.appendChild(el('p', 'audit-total', `Pérdida estimada en alertas: ${money(r.perdida_total_mxn)}`));
    }
    if (typeof r.gasto_usd === 'number') {
      bubble.appendChild(el('p', 'audit-total', `Gasto de la consulta al agente: US$${r.gasto_usd.toFixed(4)}`));
    }
  }

  function timeNow() {
    return new Intl.DateTimeFormat('es-CO', { hour: '2-digit', minute: '2-digit' }).format(new Date());
  }

  function scrollToLatest() {
    const area = document.querySelector('.chat-area');
    if (area) area.scrollTop = area.scrollHeight;
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  }

  function addMessage(role, text, options = {}) {
    welcomeBlock.classList.add('hidden');
    const message = document.createElement('article');
    message.className = `message ${role}${options.error ? ' error' : ''}`;

    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = role === 'user' ? 'T' : role === 'system' ? '!' : 'A';
    avatar.setAttribute('aria-hidden', 'true');

    const body = document.createElement('div');
    body.className = 'message-body';

    const meta = document.createElement('div');
    meta.className = 'message-meta';
    const name = document.createElement('strong');
    name.textContent = role === 'user' ? 'Tú' : role === 'system' ? 'Aviso del sistema' : 'AudiBot';
    const time = document.createElement('time');
    time.textContent = timeNow();
    meta.append(name, time);

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    if (typeof options.render === 'function') {
      bubble.classList.add('rich');
      options.render(bubble);
    } else {
      bubble.textContent = text;
    }

    body.append(meta, bubble);

    if (role === 'user') {
      const actions = el('div', 'message-actions');

      const copyBtn = el('button', 'message-action', 'Copiar');
      copyBtn.type = 'button';

      copyBtn.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(bubble.textContent);  
          toast('Mensaje copiado.', 'success');
        } catch (_) {
          toast('No se pudo copiar el mensaje.', 'error');
        }
      });

      const editBtn = el('button', 'message-action', 'Editar');
      editBtn.type = 'button';

      editBtn.addEventListener('click', () => {
        if (message.querySelector('.message-editor')) return;

        const editor = el('textarea', 'message-editor');
        editor.value = bubble.textContent;
        editor.rows = 3;

        const editActions = el('div', 'message-actions');
        const cancelBtn = el('button', 'message-action', 'Cancelar');
        const resendBtn = el('button', 'message-action', 'Guardar y reenviar');

        cancelBtn.type = 'button';
        resendBtn.type = 'button';

        const cerrarEditor = () => {
          editor.remove();
          editActions.remove();
          bubble.classList.remove('hidden');
          actions.classList.remove('hidden');
        };

        cancelBtn.addEventListener('click', cerrarEditor);

        resendBtn.addEventListener('click', () => {
          const nuevoTexto = editor.value.trim();

          if (!nuevoTexto) {
            toast('El mensaje no puede estar vacío.', 'error');
            return;
          }
          if (activeController || lastServerState === 'trabajando' || lastServerState === 'deteniendo') {
            toast('Espera a que termine o detén la auditoría actual.', 'error');
            return;
          }

          // 1) La MISMA burbuja pasa a mostrar el texto nuevo .
          bubble.textContent = nuevoTexto;
          if (!meta.querySelector('.message-edited')) {
            meta.appendChild(el('em', 'message-edited', ' · editado'));
          }
          cerrarEditor();

          // 2) Se quita lo que venía después,
          //    como al editar en cualquier chat: la conversación continúa desde este mensaje.
          while (message.nextElementSibling) message.nextElementSibling.remove();

          // 3) Se vuelve a consultar con el texto nuevo, SIN crear una burbuja de usuario nueva.
          sendOrder(nuevoTexto, { reutilizar: true });
        });

        editActions.append(cancelBtn, resendBtn);
        bubble.classList.add('hidden');
        actions.classList.add('hidden');
        body.append(editor, editActions);
        editor.focus();
      });

      actions.append(copyBtn, editBtn);
      body.appendChild(actions);
    }

    message.append(avatar, body);
    messageList.appendChild(message);
    scrollToLatest();
    return message;
  }

  function toast(message, type = '') {
    const item = document.createElement('div');
    item.className = `toast ${type}`;
    item.textContent = message;
    toastStack.appendChild(item);
    window.setTimeout(() => item.remove(), 4500);
  }

  function setConnected(connected) {
    connectionDot.classList.toggle('online', connected);
    connectionDot.classList.toggle('offline', !connected);
    connectionText.textContent = connected ? 'Servidor conectado' : 'Servidor sin conexión';
    if (!connected) headerStatus.textContent = 'No se pudo conectar al servidor';
    else if (!listening) headerStatus.textContent = 'Listo para recibir órdenes';
  }

  function setBusy(busy) {
    typingRow.classList.toggle('hidden', !busy);

    const label = sendBtn.querySelector('span:first-child');
    const arrow = sendBtn.querySelector('.send-arrow');

    if (stopping) {
      label.textContent = 'Deteniendo…';
      arrow.textContent = '■';
      sendBtn.disabled = true;
      sendBtn.classList.add('stop-mode');
    } else if (busy) {
      label.textContent = 'Detener';
      arrow.textContent = '■';
      sendBtn.disabled = false;
      sendBtn.classList.add('stop-mode');
    } else {
      label.textContent = 'Enviar orden';
      arrow.textContent = '↑';
      sendBtn.disabled = false;
      sendBtn.classList.remove('stop-mode');
    }

    if (busy || stopping) {
      headerStatus.textContent = stopping ? 'Deteniendo auditoría…' : 'Procesando solicitud';
    } else {
      headerStatus.textContent = 'Listo para recibir órdenes';
    }

    scrollToLatest();
  }

  // opciones.reutilizar: la burbuja del usuario ya existe (se está editando), no se crea otra.
  async function sendOrder(rawText, opciones = {}) {
    const text = String(rawText || '').trim();
    if (!text) return;
    if (!opciones.reutilizar) {
      addMessage('user', text);
      input.value = '';          // al editar no se toca lo que estés escribiendo abajo
      resizeInput();
    }
    activeController = new AbortController();   // marca que hay una orden en curso
    setBusy(true);

    try {
      const response = await fetch('/api/orden', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ texto: text }),
        signal: activeController.signal
      });
      let data = {};
      try { data = await response.json(); } catch (_) {}

      if (response.status === 409) {
        addMessage('system', data.error || 'AudiBot ya está procesando otra orden. Espera a que termine y vuelve a intentarlo.');
        toast('Hay otra orden en curso.', 'error');
        return;
      }
      if (!response.ok) {
        const detail = data.error || data.mensaje || `Error ${response.status}`;
        throw new Error(detail);
      }

      if (data.cancelado) {      // el servidor confirma que se detuvo
        addMessage('system', data.mensaje || 'Consulta detenida.');
        toast('Consulta detenida.');
        return;
      }

      const fallback = data.mensaje || data.respuesta || 'Orden recibida. AudiBot comenzará a procesarla.';
      if (data.resultado && Array.isArray(data.resultado.ordenes)) {
        addMessage('assistant', fallback, { render: (bubble) => renderResultado(bubble, data.resultado) });
      } else {
        addMessage('assistant', fallback);
      }
      toast('Orden enviada correctamente.', 'success');
      if (typeof data.id !== 'undefined') {
        try {
          await fetch('/api/ack', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: data.id })
          });
        } catch (_) { /* El endpoint de confirmación puede ser opcional. */ }
      }
    } catch (error) {
      addMessage('system', `No pude enviar la orden. ${error.message || 'Verifica que servidor.py esté ejecutándose.'}`, { error: true });
      toast('No se pudo enviar la orden.', 'error');
      setConnected(false);
    } finally {
      activeController = null;   // la orden terminó (o falló o se detuvo)
      stopping = false;
      setBusy(false);
    }
  }

  function resizeInput() {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();

    // Primero comprobar si hay una auditoría que detener.
    if (activeController || lastServerState === 'trabajando') {
      if (stopping) return;

      stopping = true;
      setBusy(true);

      try {
        const response = await fetch('/api/detener', { method: 'POST' });
        const data = await response.json();

        if (!response.ok) {
          stopping = false;
          setBusy(true);
          toast(data.error || 'No se pudo detener la auditoría.', 'error');
          return;
        }
        // El aviso "Consulta detenida" llega como respuesta de la propia orden.
      } catch (_) {
        stopping = false;
        setBusy(true);
        toast('No se pudo contactar con el servidor.', 'error');
      }

      return;
    }

    // Solo exigir texto cuando vamos a enviar una orden nueva.
    if (!input.value.trim()) {
      input.focus();
      toast('Escribe una orden antes de enviarla.', 'error');
      return;
    }

    sendOrder(input.value);
  });

  input.addEventListener('input', resizeInput);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  document.querySelectorAll('[data-prompt]').forEach((button) => {
    button.addEventListener('click', () => {
      input.value = button.dataset.prompt || '';
      resizeInput();
      input.focus();
    });
  });

  $('newChatBtn').addEventListener('click', () => {
    messageList.replaceChildren();
    welcomeBlock.classList.remove('hidden');
    input.value = '';
    resizeInput();
    input.focus();
    headerStatus.textContent = 'Nueva conversación';
  });

  $('dismissVoice').addEventListener('click', () => $('voiceBanner').classList.add('hidden'));

  function setupSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      voiceBtn.title = 'El dictado del navegador no está disponible';
      voiceBannerText.textContent = 'El dictado por voz no está disponible en este navegador. Escribe tu orden en el cuadro de texto.';
      voiceBtn.addEventListener('click', () => toast('Dictado no compatible con este navegador. Escribe tu orden.', 'error'));
      return;
    }

    recognition = new SpeechRecognition();
    recognition.lang = 'es-CO';
    recognition.interimResults = true;
    recognition.continuous = false;

    recognition.onstart = () => {
      listening = true;
      voiceBtn.classList.add('listening');
      voiceBtnText.textContent = 'Escuchando…';
      headerStatus.textContent = 'Escuchando dictado';
      voiceBannerText.textContent = 'Te estoy escuchando. Habla con claridad y espera a que termine la transcripción.';
    };

    recognition.onresult = (event) => {
      let transcript = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      input.value = transcript.trim();
      resizeInput();
    };

    recognition.onerror = (event) => {
      const message = event.error === 'not-allowed'
        ? 'Debes permitir el acceso al micrófono para dictar una orden.'
        : 'No pude reconocer la voz. Intenta otra vez o escribe tu orden.';
      toast(message, 'error');
    };

    recognition.onend = () => {
      listening = false;
      voiceBtn.classList.remove('listening');
      voiceBtnText.textContent = 'Voz';
      headerStatus.textContent = 'Listo para recibir órdenes';
      if (input.value.trim()) {
        voiceBannerText.textContent = 'Transcripción lista. Revisa el texto y pulsa “Enviar orden”.';
        input.focus();
      } else {
        voiceBannerText.textContent = 'También puedes pulsar “Voz” y dictar tu orden.';
      }
    };

    voiceBtn.addEventListener('click', () => {
      if (listening) {
        recognition.stop();
        return;
      }
      try { recognition.start(); }
      catch (_) { toast('El micrófono ya está activo. Espera un momento e inténtalo de nuevo.', 'error'); }
    });
  }

  async function pollServer() {
    try {
      const response = await fetch('/api/estado', { cache: 'no-store' });
      if (!response.ok) throw new Error('Servidor no disponible');
      const data = await response.json();
      setConnected(true);

      const state = data.estado;
      if (state !== 'trabajando' && state !== 'deteniendo') stopping = false;   // ya terminó o se detuvo
      if (state && state !== lastServerState) {
        lastServerState = state;
        if (state === 'trabajando') setBusy(true);
        else if (state === 'reposo') setBusy(false);
      }
    } catch (_) {
      setConnected(false);
    }
  }

  setupSpeechRecognition();
  pollServer();
  window.setInterval(pollServer, 2500);
})();