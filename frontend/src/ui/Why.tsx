import { Children, isValidElement, type ReactNode } from 'react';
import { CircleHelp } from 'lucide-react';
import { describeRef } from '../lib/why';
import { useUi } from '../store/ui';
import './why.css';

const contentText = (children: ReactNode): string => Children.toArray(children).map((child): string =>
  isValidElement<{ children?: ReactNode }>(child) ? contentText(child.props.children) : String(child)).join('');

export function Why({ refId, className = '', children, value }: { refId: string; className?: string; children: ReactNode; value?: string }) {
  const set = useUi((s) => s.set);
  return (
    <button type="button" className={`why ${className}`} aria-label={`Why is ${describeRef(refId)} ${value ?? contentText(children)}? Open the explanation`}
      onClick={(e) => { e.stopPropagation(); set({ whyRef: refId }); }} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); }}>
      {children}<CircleHelp size={12} aria-hidden />
    </button>
  );
}
