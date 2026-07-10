/** Show setup banner on pages when onboarding is incomplete. */
(function () {
  if (window.location.pathname === '/config/setup' || window.location.pathname === '/setup') return;

  fetch('/api/setup/status')
    .then(function (r) { return r.json(); })
    .then(function (j) {
      if (!j.ok || !j.show_banner) return;
      var main = document.querySelector('main.container, main');
      if (!main) return;
      var bar = document.createElement('div');
      bar.className = 'setup-banner';
      bar.innerHTML =
        '<span><strong>Finish setup</strong> — add a camera and connect Telegram to get alerts. ' +
        '<a href="/config/setup">Open getting started →</a></span>' +
        '<button type="button" class="setup-banner-dismiss" aria-label="Dismiss">Later</button>';
      main.insertBefore(bar, main.firstChild);
      bar.querySelector('.setup-banner-dismiss').addEventListener('click', function () {
        bar.remove();
        fetch('/api/setup/complete', { method: 'POST' }).catch(function () {});
      });
    })
    .catch(function () {});

})();
