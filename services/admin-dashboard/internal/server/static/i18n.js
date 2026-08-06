(function() {
  'use strict';

  var translations = {};
  var currentLocale = localStorage.getItem('admin_lang') || 'ru';

  function loadTranslations() {
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '/i18n.json?_t=' + Date.now(), false);
    try {
      xhr.send();
      if (xhr.status === 200) {
        var data = JSON.parse(xhr.responseText);
        translations = data.translations;
        currentLocale = localStorage.getItem('admin_lang') || data.locale || 'ru';
      }
    } catch(e) {
      console.warn('Failed to load i18n.json:', e);
    }
  }

  loadTranslations();

  window.__ = function(key) {
    if (translations[currentLocale] && translations[currentLocale][key] !== undefined) {
      return translations[currentLocale][key];
    }
    if (translations['ru'] && translations['ru'][key] !== undefined) {
      return translations['ru'][key];
    }
    return key;
  };

  window.__setLocale = function(locale) {
    currentLocale = locale;
    localStorage.setItem('admin_lang', locale);
  };

  window.__getLocale = function() {
    return currentLocale;
  };

  document.addEventListener('alpine:init', function() {
    window.Alpine.magic('__', function() {
      return function(key) {
        return window.__(key);
      };
    });
    window.Alpine.store('i18n', {
      locale: currentLocale,
      setLocale: function(locale) {
        window.__setLocale(locale);
        this.locale = locale;
      }
    });
  });
})();
