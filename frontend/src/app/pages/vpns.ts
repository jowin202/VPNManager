import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription, debounceTime, Subject, switchMap, catchError, of, timer, takeWhile } from 'rxjs';

import { Api, Job, ParsedConfig, Vpn, errorText } from '../api';

const NAME_RE = /^[A-Za-z][A-Za-z0-9_]{0,15}$/;

@Component({
  selector: 'app-vpns',
  imports: [FormsModule],
  template: `
    <div class="head">
      <h1>VPNs</h1>
      <button (click)="load()" [disabled]="loading()">Refresh</button>
    </div>
    @if (listError()) { <p class="msg error">{{ listError() }}</p> }

    <div class="grid">
      @for (v of vpns(); track v.name) {
        <article class="card">
          <div class="head">
            <h2>{{ v.name }}</h2>
            <button class="danger" (click)="askDelete(v)" [disabled]="busy()">Delete</button>
          </div>
          @if (!v.complete) { <p class="msg warn">Incomplete. Delete it and create it again.</p> }
          <p>
            <span class="pill" [class.ok]="v.gateway.state === 'online'" [class.bad]="v.gateway.state === 'offline' || v.gateway.state === 'missing'" [class.warn]="v.gateway.state === 'warning' || v.gateway.state === 'unknown' || v.gateway.state === 'starting'">Gateway {{ gwLabel[v.gateway.state] }}</span>
            <span class="pill" [class.ok]="hsOk(v)" [class.warn]="!hsOk(v)">{{ hsText(v) }}</span>
          </p>
          <dl>
            <dt>Endpoint</dt><dd class="mono">{{ v.endpoint || '–' }}</dd>
            <dt>Interface</dt><dd class="mono">{{ v.interface || '–' }} / {{ v.device || '–' }}</dd>
            <dt>Tunnel</dt><dd class="mono">{{ v.addresses.join(', ') || '–' }}</dd>
            <dt>Gateway</dt><dd class="mono">{{ v.gateway_name }} ({{ v.gateway_ip || '?' }})</dd>
            <dt>Provider DNS</dt><dd class="mono">{{ v.dns.join(', ') || '–' }}</dd>
            <dt>Hosts</dt><dd>{{ v.host_count }}</dd>
          </dl>
        </article>
      } @empty {
        @if (!loading()) { <p class="muted">No VPNs yet.</p> }
      }
    </div>

    @if (deleting(); as d) {
      <section class="card confirm">
        <h2>Delete VPN "{{ d.name }}"?</h2>
        @if (d.hosts.length) {
          <p>These hosts are assigned and will use the regular WAN connection afterwards:</p>
          <p class="mono">{{ d.hosts.join(', ') }}</p>
        }
        <p class="muted">This deletes the rules, NAT, aliases, gateway, interface assignment, instance and peer.</p>
        <div class="row">
          <button class="danger" (click)="confirmDelete(d)" [disabled]="busy()">Delete permanently</button>
          <button (click)="deleting.set(null)" [disabled]="busy()">Cancel</button>
        </div>
      </section>
    }

    <section class="card">
      <h2>Add VPN</h2>
      <form (ngSubmit)="submit()" class="stack">
        <label>Name
          <input name="name" [(ngModel)]="name" maxlength="16" placeholder="e.g. proton_nl" autocomplete="off" />
          @if (name() && !nameValid()) { <small class="bad-text">Only A–Z, a–z, 0–9 and _; must start with a letter; max. 16 characters.</small> }
        </label>
        <label>WireGuard config (wg-quick)
          <textarea name="config" [ngModel]="config()" (ngModelChange)="onConfig($event)" rows="12" spellcheck="false"
            placeholder="[Interface]&#10;PrivateKey = …&#10;Address = 10.2.0.2/32&#10;DNS = 10.2.0.1&#10;&#10;[Peer]&#10;PublicKey = …&#10;AllowedIPs = 0.0.0.0/0, ::/0&#10;Endpoint = host:51820"></textarea>
        </label>
        <div><label class="button">Load .conf file<input type="file" accept=".conf,text/plain" hidden (change)="loadFile($event)" /></label></div>

        @if (parseError()) { <p class="msg error">{{ parseError() }}</p> }
        @if (parsed(); as p) {
          <div class="msg info">
            Endpoint <span class="mono">{{ p.peer.endpoint }}</span> · Tunnel <span class="mono">{{ p.addresses.join(', ') }}</span>
            · AllowedIPs <span class="mono">{{ p.peer.allowed_ips.join(', ') }}</span>
            · PSK {{ p.peer.preshared_key ? 'set' : 'none' }} · Keepalive {{ p.peer.keepalive }}s
            @if (p.dns.length) { · provider DNS <span class="mono">{{ p.dns.join(', ') }}</span> (not applied to the system) }
          </div>
          @if (p.rewritten_from) {
            <p class="msg warn">The tunnel address {{ p.rewritten_from }} is already used by another VPN
              (all Proton configs share it). <span class="mono">{{ p.addresses[0] }}</span> is used instead.</p>
          }
          @if (p.dropped_ipv6.length) {
            <p class="msg warn">The IPv6 address {{ p.dropped_ipv6.join(', ') }} is already used by another VPN
              and is dropped. The VPN Manager only routes IPv4 through the tunnel.</p>
          }
          <div class="row">
            <label>Tunnel address <small class="muted">(optional)</small>
              <input name="tunnel" [(ngModel)]="tunnelAddress" [placeholder]="p.addresses[0]" autocomplete="off" />
            </label>
            <label>Gateway IP <small class="muted">(optional)</small>
              <input name="gw" [(ngModel)]="gatewayIp" [placeholder]="p.gateway_ip" autocomplete="off" />
            </label>
          </div>
        }
        @if (pendingConflict()) {
          <label class="check"><input type="checkbox" name="allowPending" [(ngModel)]="allowPending" />
            Also apply the pending interface changes in OPNsense</label>
        }
        <div><button type="submit" class="primary" [disabled]="!canSubmit()">Create VPN</button></div>
      </form>
      @if (createError()) { <p class="msg error">{{ createError() }}</p> }

      @if (job(); as j) {
        <div class="progress">
          <p><strong>{{ jobTitle(j) }}</strong> – step {{ j.completed }} of {{ j.total }}</p>
          <ol class="steps">
            @for (s of j.steps; track s.key) {
              <li [class]="s.status"><span class="dot"></span>{{ s.title }}@if (s.detail) { <small> – {{ s.detail }}</small> }</li>
            }
          </ol>
          @if (j.error) { <p class="msg error">{{ j.error.message }}</p> }
          @if (j.rollback.length) {
            <p><strong>Rollback</strong></p>
            <ul>@for (r of j.rollback; track $index) { <li>{{ r.action }}: {{ r.status }}@if (r.error) { – {{ r.error }} }</li> }</ul>
          }
          @for (w of j.warnings; track w) { <p class="msg warn">{{ w }}</p> }
        </div>
      }
    </section>
  `,
})
export class VpnsPage implements OnInit, OnDestroy {
  private api = inject(Api);
  protected vpns = signal<Vpn[]>([]);
  protected loading = signal(false);
  protected listError = signal<string | null>(null);

  protected name = signal('');
  protected config = signal('');
  protected gatewayIp = signal('');
  protected tunnelAddress = signal('');
  protected allowPending = signal(false);
  protected pendingConflict = signal(false);
  protected parsed = signal<ParsedConfig | null>(null);
  protected parseError = signal<string | null>(null);
  protected createError = signal<string | null>(null);
  protected job = signal<Job | null>(null);
  protected busy = signal(false);
  protected deleting = signal<{ name: string; hosts: string[] } | null>(null);

  protected nameValid = computed(() => NAME_RE.test(this.name()));
  protected canSubmit = computed(() => this.nameValid() && !!this.parsed() && !this.busy());
  protected gwLabel: Record<string, string> = { online: 'online', offline: 'offline', warning: 'degraded', unknown: 'unknown', missing: 'missing', starting: '– monitoring starting' };

  private configChanges = new Subject<string>();
  private subs = new Subscription();

  ngOnInit() {
    this.subs.add(timer(0, 15000).subscribe(() => { if (!this.busy()) this.load(); }));
    this.subs.add(this.configChanges.pipe(
      debounceTime(300),
      switchMap(text => text.trim()
        ? this.api.parse(text).pipe(catchError((e: unknown) => of(e instanceof HttpErrorResponse ? e : null)))
        : of(null)),
    ).subscribe(res => {
      if (res instanceof HttpErrorResponse) { this.parsed.set(null); this.parseError.set(errorText(res)); }
      else { this.parsed.set(res); this.parseError.set(null); }
    }));
  }

  ngOnDestroy() { this.subs.unsubscribe(); }

  load() {
    this.loading.set(true);
    this.api.vpns().subscribe({
      next: v => { this.vpns.set(v); this.listError.set(null); this.loading.set(false); },
      error: e => { this.listError.set(errorText(e)); this.loading.set(false); },
    });
  }

  onConfig(text: string) {
    this.config.set(text);
    this.configChanges.next(text);
  }

  loadFile(ev: Event) {
    const file = (ev.target as HTMLInputElement).files?.[0];
    if (!file) return;
    file.text().then(t => {
      this.onConfig(t);
      if (!this.name()) this.name.set(file.name.replace(/\.conf$/i, '').replace(/[^A-Za-z0-9_]/g, '_').slice(0, 16));
    });
  }

  submit() {
    this.busy.set(true);
    this.createError.set(null);
    this.job.set(null);
    this.api.createVpn({
      name: this.name(), config: this.config(),
      gateway_ip: this.gatewayIp().trim() || null, tunnel_address: this.tunnelAddress().trim() || null,
      allow_pending_changes: this.allowPending(),
    }).subscribe({
      next: ({ job_id, job }) => { this.job.set(job); this.pendingConflict.set(false); this.poll(job_id); },
      error: (e: HttpErrorResponse) => {
        this.busy.set(false);
        this.createError.set(errorText(e));
        if (e.status === 409 && e.error?.details?.pending) this.pendingConflict.set(true);
      },
    });
  }

  private poll(id: string) {
    this.subs.add(timer(0, 1000).pipe(
      switchMap(() => this.api.job(id)),
      takeWhile(j => j.status === 'running', true),
    ).subscribe({
      next: j => {
        this.job.set(j);
        if (j.status !== 'running') {
          this.busy.set(false);
          if (j.status === 'succeeded') { this.name.set(''); this.config.set(''); this.parsed.set(null); this.gatewayIp.set(''); this.tunnelAddress.set(''); }
          this.load();
        }
      },
      error: e => { this.busy.set(false); this.createError.set(errorText(e)); },
    }));
  }

  askDelete(v: Vpn) { this.deleting.set({ name: v.name, hosts: v.hosts }); }

  confirmDelete(d: { name: string; hosts: string[] }) {
    this.busy.set(true);
    this.api.deleteVpn(d.name, true).subscribe({
      next: j => { this.busy.set(false); this.deleting.set(null); this.job.set(j); this.load(); },
      error: e => { this.busy.set(false); this.listError.set(errorText(e)); this.deleting.set(null); },
    });
  }

  hsOk(v: Vpn) { return v.handshake_age !== null && v.handshake_age <= 180; }

  hsText(v: Vpn) {
    if (v.handshake_age === null) return 'no handshake';
    const a = v.handshake_age;
    return `handshake ${a < 120 ? a + ' s' : Math.round(a / 60) + ' min'} ago`;
  }

  jobTitle(j: Job) {
    const what = j.kind === 'create' ? 'Creating' : 'Deleting';
    const state = j.status === 'running' ? 'in progress' : j.status === 'succeeded' ? 'succeeded' : 'failed';
    return `${what} "${j.name}" ${state}`;
  }
}
