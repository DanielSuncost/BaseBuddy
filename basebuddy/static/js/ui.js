/**
 * Shared UI helpers — toasts, alerts, confirm dialogs.
 */
(function (global) {
  var ICONS = {
    success: 'check_circle',
    error: 'error',
    warning: 'warning',
    info: 'info',
  };

  function ensureContainer() {
    var el = document.getElementById('bb-toast-container');
    if (!el) {
      el = document.createElement('div');
      el.id = 'bb-toast-container';
      el.setAttribute('aria-live', 'polite');
      el.setAttribute('aria-atomic', 'true');
      document.body.appendChild(el);
    }
    return el;
  }

  function showToast(message, type, options) {
    type = type || 'info';
    options = options || {};
    var duration = options.duration != null ? options.duration : 4500;

    var container = ensureContainer();
    var toast = document.createElement('div');
    toast.className = 'bb-toast bb-toast--' + type;
    toast.setAttribute('role', 'alert');

    var icon = document.createElement('span');
    icon.className = 'material-icons-outlined';
    icon.textContent = ICONS[type] || ICONS.info;
    toast.appendChild(icon);

    var msg = document.createElement('span');
    msg.className = 'bb-toast-message';
    msg.textContent = message;
    toast.appendChild(msg);

    container.appendChild(toast);

    if (duration > 0) {
      setTimeout(function () {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(8px)';
        toast.style.transition = 'opacity 0.2s, transform 0.2s';
        setTimeout(function () {
          if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 220);
      }, duration);
    }

    return toast;
  }

  function toggleTheme() {
    var root = document.documentElement;
    var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try {
      localStorage.setItem('bb-theme', next);
    } catch (e) { /* ignore */ }
  }

  function initThemeToggle() {
    var btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.addEventListener('click', toggleTheme);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initThemeToggle);
  } else {
    initThemeToggle();
  }

  global.showToast = showToast;
  global.toggleTheme = toggleTheme;
})(typeof window !== 'undefined' ? window : this);
