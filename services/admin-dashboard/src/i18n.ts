// i18n.ts — synchronous translation loader
// Loads /i18n.json synchronously before Alpine boots.
// Exposes __(key) globally and via $magic('__')

type Translations = Record<string, Record<string, string>>;

let translations: Translations = {};
let currentLocale = localStorage.getItem('admin_lang') || 'ru';

function loadTranslations(): void {
  const xhr = new XMLHttpRequest();
  xhr.open('GET', '/i18n.json?_t=' + Date.now(), false);
  try {
    xhr.send();
    if (xhr.status === 200) {
      const data = JSON.parse(xhr.responseText) as { translations?: Translations; locale?: string };
      translations = data.translations ?? {};
      currentLocale = localStorage.getItem('admin_lang') || data.locale || 'ru';
    }
  } catch {
    console.warn('Failed to load i18n.json');
  }
}

loadTranslations();

const t = (key: string): string => {
  const localeT = translations[currentLocale];
  if (localeT?.[key] !== undefined) return localeT[key]!;
  const ruT = translations['ru'];
  if (ruT?.[key] !== undefined) return ruT[key]!;
  return key;
};

const setLocale = (locale: string): void => {
  currentLocale = locale;
  localStorage.setItem('admin_lang', locale);
};

const getLocale = (): string => currentLocale;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any;
w.__ = t;
w.__setLocale = setLocale;
w.__getLocale = getLocale;

document.addEventListener('alpine:init', () => {
  Alpine.magic('__', () => (key: string) => t(key));
  Alpine.store('i18n', {
    locale: currentLocale,
    setLocale(locale: string): void { setLocale(locale); this.locale = locale; },
  });
});

export {};
