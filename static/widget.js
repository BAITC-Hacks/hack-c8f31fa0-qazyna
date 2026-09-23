(() => {
  if (document.getElementById('ekt-chat-widget')) return;
  const base = new URL(document.currentScript.src).origin;
  const host = document.createElement('div');
  host.id = 'ekt-chat-widget';
  const root = host.attachShadow({mode: 'open'});
  const style = document.createElement('style');
  style.textContent = `:host{all:initial;position:fixed;right:16px;bottom:16px;z-index:2147483000;font:16px system-ui}
button{border:0;border-radius:28px;background:#146552;color:white;padding:16px 22px;font:600 16px system-ui;cursor:pointer;box-shadow:0 6px 24px #0003}
button:focus-visible{outline:3px solid #e5aa31;outline-offset:3px}
iframe{position:absolute;bottom:66px;right:0;width:min(380px,calc(100vw - 32px));height:min(620px,calc(100dvh - 110px));max-height:calc(100vh - 110px);border:0;border-radius:16px;background:white;box-shadow:0 12px 48px #0004}iframe[hidden]{display:none}`;
  const button = document.createElement('button');
  button.type = 'button'; button.textContent = 'Чат ekt.kz';
  button.setAttribute('aria-expanded', 'false'); button.setAttribute('aria-label', 'Открыть чат ekt.kz');
  const frame = document.createElement('iframe');
  frame.title = 'Консультант ekt.kz'; frame.hidden = true;
  frame.src = base + '/widget.html?site=' + encodeURIComponent(location.origin);
  const toggle = (open) => {frame.hidden = !open; button.setAttribute('aria-expanded', String(open)); if(!open) button.focus();};
  button.addEventListener('click', () => toggle(frame.hidden));
  window.addEventListener('message', (event) => {
    if (event.origin === base && event.source === frame.contentWindow && event.data?.type === 'ekt-close') toggle(false);
  });
  root.append(style, frame, button);
  (document.body || document.documentElement).append(host);
})();
