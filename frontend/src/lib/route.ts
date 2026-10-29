import { useEffect, useState } from 'react';

export type Page = 'live' | 'incidents' | 'savings' | 'forecast' | 'lab' | 'executive' | 'audit';
export const PAGES: Page[] = ['live', 'incidents', 'savings', 'forecast', 'lab', 'executive', 'audit'];

export interface Route { page: Page; id?: string }

export const parse = (hash: string): Route => {
  const [, page, id] = hash.replace(/^#/, '').split('/');
  return { page: (PAGES as string[]).includes(page) ? (page as Page) : 'live', id: id || undefined };
};
export const href = (page: Page, id?: string) => `#/${page}${id ? `/${id}` : ''}`;
export const go = (page: Page, id?: string) => { window.location.hash = href(page, id); };

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash));
  useEffect(() => {
    const on = () => { setRoute(parse(window.location.hash)); window.scrollTo({ top: 0 }); };
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return route;
}
