import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './en.json';
import ar from './ar.json';

void i18n.use(initReactI18next).init({ resources: { en: { translation: en }, ar: { translation: ar } }, lng: 'en', fallbackLng: 'en', interpolation: { escapeValue: false } });
const applyDirection = (language: string) => {
  document.documentElement.lang = language;
  document.documentElement.dir = i18n.dir(language);
  document.title = i18n.t('app.title');
};
i18n.on('languageChanged', applyDirection);
applyDirection(i18n.language);
export default i18n;
