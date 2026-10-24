// Spoken languages. `speech` is the BCP-47 tag the browser's recogniser wants,
// `code` is what the translation services want. The target is always Hebrew.
export const LANGUAGES = [
  { code: 'en', speech: 'en-US', he: 'אנגלית', native: 'English' },
  { code: 'ar', speech: 'ar-SA', he: 'ערבית', native: 'العربية' },
  { code: 'ru', speech: 'ru-RU', he: 'רוסית', native: 'Русский' },
  { code: 'fr', speech: 'fr-FR', he: 'צרפתית', native: 'Français' },
  { code: 'es', speech: 'es-ES', he: 'ספרדית', native: 'Español' },
  { code: 'de', speech: 'de-DE', he: 'גרמנית', native: 'Deutsch' },
  { code: 'it', speech: 'it-IT', he: 'איטלקית', native: 'Italiano' },
  { code: 'pt', speech: 'pt-BR', he: 'פורטוגזית', native: 'Português' },
  { code: 'tr', speech: 'tr-TR', he: 'טורקית', native: 'Türkçe' },
  { code: 'uk', speech: 'uk-UA', he: 'אוקראינית', native: 'Українська' },
  { code: 'pl', speech: 'pl-PL', he: 'פולנית', native: 'Polski' },
  { code: 'nl', speech: 'nl-NL', he: 'הולנדית', native: 'Nederlands' },
  { code: 'ro', speech: 'ro-RO', he: 'רומנית', native: 'Română' },
  { code: 'hi', speech: 'hi-IN', he: 'הינדית', native: 'हिन्दी' },
  { code: 'ja', speech: 'ja-JP', he: 'יפנית', native: '日本語' },
  { code: 'ko', speech: 'ko-KR', he: 'קוריאנית', native: '한국어' },
  { code: 'zh', speech: 'zh-CN', he: 'סינית', native: '中文' },
];

export const DEFAULT_LANGUAGE = 'en';

export function findLanguage(code) {
  return LANGUAGES.find((l) => l.code === code) || LANGUAGES.find((l) => l.code === DEFAULT_LANGUAGE);
}
