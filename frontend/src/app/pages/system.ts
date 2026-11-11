import { Component, OnInit, inject, signal } from '@angular/core';

import { Api, SystemCheck, errorText } from '../api';

@Component({
  selector: 'app-system',
  template: `
    <div class="head">
      <h1>System status</h1>
      <button (click)="load()" [disabled]="loading()">Check again</button>
    </div>
    @if (error()) { <p class="msg error">{{ error() }}</p> }
    @if (check(); as c) {
      <section class="card">
        <p>
          <span class="pill" [class.ok]="c.ok" [class.bad]="!c.ok">{{ c.ok ? 'Ready' : 'Not ready' }}</span>
          {{ c.opnsense_url }}
        </p>
        <dl>
          <dt>Reachable</dt><dd>{{ c.reachable ? 'yes' : 'no' }}</dd>
          <dt>Version</dt>
          <dd [class.bad-text]="!c.version_ok">{{ c.version || 'unknown' }} (minimum {{ c.min_version }})</dd>
        </dl>
        @if (c.error) { <p class="msg error">{{ c.error }}</p> }
        @for (w of c.warnings; track w) { <p class="msg warn">{{ w }}</p> }
      </section>
      @if (c.privileges.length) {
        <section class="card">
          <h2>Privileges</h2>
          <table>
            <thead><tr><th>Area</th><th>Endpoint</th><th>Status</th></tr></thead>
            <tbody>
              @for (p of c.privileges; track p.endpoint) {
                <tr>
                  <td>{{ p.name }}</td><td class="mono">{{ p.endpoint }}</td>
                  <td><span class="pill" [class.ok]="p.status === 'ok'" [class.bad]="p.status === 'missing'" [class.warn]="p.status !== 'ok' && p.status !== 'missing'">{{ label[p.status] }}</span></td>
                </tr>
              }
            </tbody>
          </table>
        </section>
      }
      <section class="card">
        <h2>Notes</h2>
        <ul>@for (h of c.hints; track h) { <li>{{ h }}</li> }</ul>
      </section>
    } @else if (loading()) { <p class="muted">Checking …</p> }
  `,
})
export class SystemPage implements OnInit {
  private api = inject(Api);
  protected check = signal<SystemCheck | null>(null);
  protected error = signal<string | null>(null);
  protected loading = signal(false);
  protected label: Record<string, string> = { ok: 'ok', missing: 'missing', unavailable: 'not available', error: 'error' };

  ngOnInit() { this.load(); }

  load() {
    this.loading.set(true);
    this.error.set(null);
    this.api.check().subscribe({
      next: c => { this.check.set(c); this.loading.set(false); },
      error: e => { this.error.set(errorText(e)); this.loading.set(false); },
    });
  }
}
