import { HttpClient, HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable } from 'rxjs';

const TOKEN_KEY = 'vpnmgr.token';

export interface Step { key: string; title: string; status: 'pending' | 'running' | 'done' | 'skipped' | 'warning' | 'failed'; detail: string | null; }
export interface Job {
  id: string; kind: string; name: string; status: 'running' | 'succeeded' | 'failed';
  steps: Step[]; total: number; completed: number;
  error: { message: string; validations?: Record<string, string> } | null;
  rollback: { action: string; status: string; error?: string }[];
  warnings: string[];
}
export interface GatewayState { state: 'online' | 'offline' | 'warning' | 'unknown' | 'missing' | 'starting'; detail: string | null; delay: string | null; loss: string | null; }
export interface Vpn {
  name: string; complete: boolean; device: string | null; interface: string | null;
  gateway_name: string; hosts_alias: string; handshake_age: number | null; gateway: GatewayState;
  hosts: string[]; host_count: number; dns: string[]; endpoint: string | null; addresses: string[];
  gateway_ip: string | null; created_at: number | null; objects: Record<string, unknown>;
}
export interface Host { ip: string; mac: string | null; hostname: string | null; interface: string | null; last_seen: string | null; online: boolean; sources: string[]; vpn: string | null; }
export interface Privilege { name: string; endpoint: string; status: 'ok' | 'missing' | 'unavailable' | 'error'; }
export interface SystemCheck {
  ok: boolean; opnsense_url: string; version: string | null; min_version: string; version_ok: boolean;
  reachable: boolean; privileges: Privilege[]; warnings: string[]; hints: string[]; error: string | null;
}
export interface ParsedConfig {
  rewritten_from: string | null;
  dropped_ipv6: string[];
  public_key: string; addresses: string[]; dns: string[]; mtu: number | null; gateway_ip: string;
  peer: { public_key: string; endpoint: string; allowed_ips: string[]; keepalive: number; preshared_key: boolean };
}
export interface AssignResult { changed: { ip: string; vpn: string | null }[]; verified: boolean; warnings: string[]; }

@Injectable({ providedIn: 'root' })
export class Auth {
  readonly token = signal<string | null>(readToken());
  readonly rejected = signal(false);

  set(token: string) {
    try { localStorage.setItem(TOKEN_KEY, token); } catch { /* private mode */ }
    this.token.set(token);
    this.rejected.set(false);
  }

  clear() {
    try { localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
    this.token.set(null);
  }
}

function readToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(Auth);
  const token = auth.token();
  const authed = token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req;
  return new Observable(sub => next(authed).subscribe({
    next: v => sub.next(v),
    error: (e: HttpErrorResponse) => {
      if (e.status === 401) { auth.rejected.set(true); auth.clear(); }
      sub.error(e);
    },
    complete: () => sub.complete(),
  }));
};

export function errorText(e: unknown): string {
  if (e instanceof HttpErrorResponse) {
    const d = e.error?.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map((x: { msg: string }) => x.msg).join('; ');
    if (e.status === 0) return 'Backend not reachable.';
    return `HTTP ${e.status}`;
  }
  return String(e);
}

@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);

  check() { return this.http.get<SystemCheck>('/api/system/check'); }
  vpns() { return this.http.get<Vpn[]>('/api/vpns'); }
  parse(config: string) { return this.http.post<ParsedConfig>('/api/vpns/parse', { config }); }
  createVpn(body: { name: string; config: string; gateway_ip?: string | null; tunnel_address?: string | null; allow_pending_changes?: boolean }) {
    return this.http.post<{ job_id: string; job: Job }>('/api/vpns', body);
  }
  job(id: string) { return this.http.get<Job>(`/api/jobs/${id}`); }
  deleteVpn(name: string, confirmHosts: boolean) {
    return this.http.delete<Job>(`/api/vpns/${encodeURIComponent(name)}`, { params: { confirm_hosts: confirmHosts } });
  }
  hosts() { return this.http.get<Host[]>('/api/hosts'); }
  assign(changes: { ip: string; vpn: string | null }[]) { return this.http.put<AssignResult>('/api/hosts/assignments', changes); }
}
