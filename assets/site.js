/* Creator Desk website. Two small, optional enhancements; everything works without JavaScript. */
(function () {
  'use strict';

  // 1) Mark the download card that matches the visitor's computer.
  var ua = navigator.userAgent || '';
  var mine = /Windows/i.test(ua) ? 'windows' : /Mac/i.test(ua) && !/iPhone|iPad/i.test(ua) ? 'mac' : null;
  if (mine) {
    var card = document.querySelector('[data-os="' + mine + '"]');
    if (card) card.classList.add('you');
  }

  // 2) If the repository has a published release, offer its files as direct download buttons.
  var box = document.querySelector('[data-release]');
  if (!box) return;
  fetch('https://api.github.com/repos/eravd33-ship-it/taco-builder/releases/latest', { headers: { Accept: 'application/vnd.github+json' } })
    .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
    .then(function (release) {
      var assets = (release.assets || []).filter(function (a) {
        return /\.(zip|exe|dmg)$/i.test(a.name) && a.browser_download_url.indexOf('https://github.com/') === 0;
      });
      if (!assets.length) return;
      var buttons = box.querySelector('[data-release-buttons]');
      assets.forEach(function (a) {
        var os = /win/i.test(a.name) ? 'Windows' : /mac|darwin|dmg/i.test(a.name) ? 'Mac' : a.name;
        var link = document.createElement('a');
        link.className = 'btn';
        link.href = a.browser_download_url;
        link.textContent = '⬇️ להורדה ל-' + os + ' (' + Math.max(1, Math.round(a.size / 1048576)) + 'MB)';
        buttons.appendChild(link);
      });
      var version = box.querySelector('[data-release-version]');
      if (version && release.tag_name) version.textContent = 'גרסה ' + release.tag_name;
      box.hidden = false;
      var fallback = document.querySelector('[data-release-fallback]');
      if (fallback) fallback.hidden = true;
    })
    .catch(function () { /* no release yet, or offline: the instructions stay */ });
})();
