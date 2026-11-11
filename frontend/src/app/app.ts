import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { Auth } from './api';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, FormsModule],
  template: `
    <header>
      <strong>OPNsense VPN Manager</strong>
      @if (auth.token()) {
        <nav>
          <a routerLink="/" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">VPNs</a>
          <a routerLink="/hosts" routerLinkActive="active">Hosts</a>
          <a routerLink="/system" routerLinkActive="active">System</a>
        </nav>
        <button class="link" (click)="auth.clear()">Sign out</button>
      }
    </header>
    <main>
      @if (auth.token()) {
        <router-outlet />
      } @else {
        <form class="card login" (ngSubmit)="login()">
          <h1>Sign in</h1>
          @if (auth.rejected()) { <p class="msg error">The token was rejected.</p> }
          <label>API token (VPNMGR_API_TOKEN)
            <input type="password" name="token" [(ngModel)]="tokenInput" autocomplete="current-password" required />
          </label>
          <button type="submit" class="primary" [disabled]="!tokenInput().trim()">Sign in</button>
        </form>
      }
    </main>
  `,
})
export class App {
  protected auth = inject(Auth);
  protected tokenInput = signal('');

  login() {
    this.auth.set(this.tokenInput().trim());
    this.tokenInput.set('');
  }
}
