import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import ar from './ar.json';

/** English copy lives in the code as default values (t('key', 'English')); ar.json carries the Arabic. */
void i18n.use(initReactI18next).init({ resources: { ar: { translation: ar } }, lng: 'en', fallbackLng: false, interpolation: { escapeValue: false }, returnEmptyString: false });
const apply = (language: string) => {
  document.documentElement.lang = language;
  document.documentElement.dir = language === 'ar' ? 'rtl' : 'ltr';
};
i18n.on('languageChanged', apply);
apply(i18n.language);
export default i18n;
