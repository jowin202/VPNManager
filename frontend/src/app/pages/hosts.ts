import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription, forkJoin, timer } from 'rxjs';

import { Api, Host, errorText } from '../api';

const NONE = '';

@Component({
  selector: 'app-hosts',
  imports: [FormsModule],
  template: `
    <div class="head">
      <h1>Host assignment</h1>
      <label class="check"><input type="checkbox" [ngModel]="autoRefresh()" (ngModelChange)="setAuto($event)" /> Auto refresh</label>
    </div>
    <p class="muted">Assignments are IP-based. Hosts need a fixed IP or a DHCP reservation. Changes take effect immediately.</p>
    @if (error()) { <p class="msg error">{{ error() }}</p> }
    @for (w of warnings(); track w) { <p class="msg warn">{{ w }}</p> }

    <div class="toolbar">
      <input type="search" placeholder="Search (IP, name, MAC)" [ngModel]="query()" (ngModelChange)="query.set($event)" />
      <select [ngModel]="filterVpn()" (ngModelChange)="filterVpn.set($event)">
        <option value="*">All</option>
        <option [value]="NONE">No VPN</option>
        @for (v of vpnNames(); track v) { <option [value]="v">{{ v }}</option> }
      </select>
      @if (selected().size) {
        <span>{{ selected().size }} selected →</span>
        <select [ngModel]="bulkTarget()" (ngModelChange)="bulkTarget.set($event)">
          <option [value]="NONE">No VPN</option>
          @for (v of vpnNames(); track v) { <option [value]="v">{{ v }}</option> }
        </select>
        <button class="primary" (click)="applyBulk()" [disabled]="saving()">Assign</button>
      }
    </div>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th><input type="checkbox" [checked]="allSelected()" (change)="toggleAll($any($event.target).checked)" aria-label="Select all" /></th>
            <th>IP</th><th>Hostname</th><th>MAC</th><th>Status</th><th>VPN</th>
          </tr>
        </thead>
        <tbody>
          @for (h of visible(); track h.ip) {
            <tr [class.saving]="savingIps().has(h.ip)">
              <td><input type="checkbox" [checked]="selected().has(h.ip)" (change)="toggle(h.ip)" [attr.aria-label]="'Select ' + h.ip" /></td>
              <td class="mono">{{ h.ip }}</td>
              <td>{{ h.hostname || '–' }}</td>
              <td class="mono">{{ h.mac || '–' }}</td>
              <td><span class="pill" [class.ok]="h.online" [class.off]="!h.online">{{ h.online ? 'online' : 'offline' }}</span></td>
              <td>
                <select [ngModel]="h.vpn ?? NONE" (ngModelChange)="change(h, $event)" [disabled]="saving()" [attr.aria-label]="'VPN for ' + h.ip">
                  <option [value]="NONE">No VPN</option>
                  @for (v of vpnNames(); track v) { <option [value]="v">{{ v }}</option> }
                </select>
              </td>
            </tr>
          } @empty {
            <tr><td colspan="6" class="muted">{{ loading() ? 'Loading …' : 'No hosts found.' }}</td></tr>
          }
        </tbody>
      </table>
    </div>
  `,
})
export class HostsPage implements OnInit, OnDestroy {
  protected readonly NONE = NONE;
  private api = inject(Api);
  protected hosts = signal<Host[]>([]);
  protected vpnNames = signal<string[]>([]);
  protected loading = signal(false);
  protected saving = signal(false);
  protected savingIps = signal(new Set<string>());
  protected error = signal<string | null>(null);
  protected warnings = signal<string[]>([]);
  protected query = signal('');
  protected filterVpn = signal('*');
  protected selected = signal(new Set<string>());
  protected bulkTarget = signal(NONE);
  protected autoRefresh = signal(true);
  private refresh?: Subscription;

  protected visible = computed(() => {
    const q = this.query().trim().toLowerCase();
    const f = this.filterVpn();
    return this.hosts().filter(h =>
      (f === '*' || (h.vpn ?? NONE) === f) &&
      (!q || [h.ip, h.hostname, h.mac].some(x => x?.toLowerCase().includes(q))));
  });
  protected allSelected = computed(() => this.visible().length > 0 && this.visible().every(h => this.selected().has(h.ip)));

  ngOnInit() { this.setAuto(true); }
  ngOnDestroy() { this.refresh?.unsubscribe(); }

  setAuto(on: boolean) {
    this.autoRefresh.set(on);
    this.refresh?.unsubscribe();
    this.refresh = on ? timer(0, 15000).subscribe(() => this.load()) : undefined;
    if (!on) this.load();
  }

  load() {
    if (this.saving()) return;
    this.loading.set(true);
    forkJoin({ hosts: this.api.hosts(), vpns: this.api.vpns() }).subscribe({
      next: ({ hosts, vpns }) => {
        this.hosts.set(hosts);
        this.vpnNames.set(vpns.map(v => v.name));
        this.error.set(null);
        this.loading.set(false);
      },
      error: e => { this.error.set(errorText(e)); this.loading.set(false); },
    });
  }

  toggle(ip: string) {
    const s = new Set(this.selected());
    s.has(ip) ? s.delete(ip) : s.add(ip);
    this.selected.set(s);
  }

  toggleAll(on: boolean) {
    this.selected.set(on ? new Set(this.visible().map(h => h.ip)) : new Set());
  }

  change(h: Host, value: string) {
    this.save([{ ip: h.ip, vpn: value || null }]);
  }

  applyBulk() {
    const vpn = this.bulkTarget() || null;
    this.save([...this.selected()].map(ip => ({ ip, vpn })), () => this.selected.set(new Set()));
  }

  private save(changes: { ip: string; vpn: string | null }[], done?: () => void) {
    const before = this.hosts();
    const target = new Map(changes.map(c => [c.ip, c.vpn]));
    // Show optimistically, revert on error
    this.hosts.set(before.map(h => target.has(h.ip) ? { ...h, vpn: target.get(h.ip)! } : h));
    this.saving.set(true);
    this.savingIps.set(new Set(target.keys()));
    this.error.set(null);
    this.api.assign(changes).subscribe({
      next: r => {
        this.saving.set(false);
        this.savingIps.set(new Set());
        this.warnings.set(r.warnings);
        done?.();
        this.load();
      },
      error: e => {
        this.hosts.set(before);
        this.saving.set(false);
        this.savingIps.set(new Set());
        this.error.set(errorText(e));
      },
    });
  }
}
