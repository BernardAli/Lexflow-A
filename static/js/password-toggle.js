(function () {
  'use strict';

  document.querySelectorAll('[data-password-toggle]').forEach(function (button) {
    var input = document.getElementById(button.dataset.passwordToggle);
    if (!input) return;

    button.hidden = false;
    button.addEventListener('click', function () {
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      button.setAttribute('aria-pressed', String(show));
      button.setAttribute('aria-label', (show ? 'Hide ' : 'Show ') + button.dataset.passwordLabel);
      var icon = button.querySelector('i');
      if (icon) icon.className = 'bi ' + (show ? 'bi-eye-slash' : 'bi-eye');
    });
  });
})();
