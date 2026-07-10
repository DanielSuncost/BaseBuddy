/**
 * Theme initialization — load in <head> before CSS paint to avoid flash.
 */
(function () {
  var STORAGE_KEY = 'bb-theme';
  var stored = null;
  try {
    stored = localStorage.getItem(STORAGE_KEY);
  } catch (e) { /* private browsing */ }

  var prefersDark =
    window.matchMedia &&
    window.matchMedia('(prefers-color-scheme: dark)').matches;

  var theme = stored === 'light' || stored === 'dark' ? stored : prefersDark ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', theme);
})();
