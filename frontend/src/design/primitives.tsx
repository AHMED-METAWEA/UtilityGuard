import { AlertTriangle, CheckCircle2, Circle, Inbox, OctagonX, RefreshCw, Unplug } from 'lucide-react';
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import type { LoadState } from '../store/slices';

export function Panel({ children, className = '', ...props }: HTMLAttributes<HTMLElement>) {
  return <section className={`panel ${className}`} {...props}>{children}</section>;
}
export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'ok' | 'warn' | 'fault' | 'water' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
export function StatusDot({ status, label }: { status: 'ok' | 'warn' | 'fault' | 'offline'; label: string }) {
  const Icon = { ok: CheckCircle2, warn: AlertTriangle, fault: OctagonX, offline: Unplug }[status];
  return <span className={`status status-${status}`}><Icon size={12} aria-hidden="true" /><span>{label}</span></span>;
}
export function Button({ children, variant = 'default', className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'default' | 'primary' | 'ghost' | 'icon' }) {
  return <button type="button" className={`button button-${variant} ${className}`} {...props}>{children}</button>;
}
export function Toggle({ checked, onChange, label, disabled = false }: { checked: boolean; onChange: () => void; label: string; disabled?: boolean }) {
  return <button type="button" className="toggle" role="switch" aria-checked={checked} aria-label={label} onClick={onChange} disabled={disabled}><span /></button>;
}
export function Skeleton({ lines = 3 }: { lines?: number }) {
  const { t } = useTranslation();
  return <div className="skeleton-group" role="status" aria-label={t('common.loading')}>{Array.from({ length: lines }, (_, i) => <div key={i} className="skeleton" style={{ width: `${100 - i % 3 * 16}%` }} />)}</div>;
}
export function EmptyState({ title, detail, icon = <Inbox size={22} /> }: { title: string; detail?: string; icon?: ReactNode }) {
  return <div className="empty-state"><span className="empty-icon" aria-hidden="true">{icon}</span><p>{title}</p>{detail && <span>{detail}</span>}</div>;
}
export function ErrorState({ onRetry, message }: { onRetry?: () => void; message?: string }) {
  const { t } = useTranslation();
  return <div className="error-state" role="status"><AlertTriangle size={18} aria-hidden="true" /><p>{t('common.unavailable')}</p><span>{message ?? t('common.unavailableDetail')}</span>{onRetry && <Button variant="ghost" onClick={onRetry}><RefreshCw size={12} />{t('common.retry')}</Button>}</div>;
}
export function DataState({ status, empty, title, detail, children, onRetry }: { status: LoadState; empty: boolean; title: string; detail?: string; children?: ReactNode; onRetry?: () => void }) {
  if (status === 'loading') return <Skeleton />;
  if (status === 'error') return <ErrorState onRetry={onRetry} />;
  if (empty) return <EmptyState title={title} detail={detail} />;
  return <>{children}</>;
}
export function Tooltip({ content, children }: { content: string; children: ReactNode }) {
  const id = useId();
  return <span className="tooltip-anchor" tabIndex={0} aria-describedby={id}>{children}<span role="tooltip" id={id} className="tooltip">{content}</span></span>;
}
export function NumberValue({ value, unit, digits = 0, className = '' }: { value: number; unit: string; digits?: number; className?: string }) {
  const { i18n } = useTranslation();
  return <Tooltip content={unit}><span className={`number-value ${className}`}>{new Intl.NumberFormat(i18n.language, { maximumFractionDigits: digits }).format(value)}<small>{unit}</small></span></Tooltip>;
}
export function PanelTitle({ title, meta, icon }: { title: string; meta?: ReactNode; icon?: ReactNode }) {
  return <div className="panel-heading"><h2>{icon}{title}</h2>{meta ?? <Circle size={6} className="text-faint" aria-hidden="true" />}</div>;
}
