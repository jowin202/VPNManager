import { Routes } from '@angular/router';

import { HostsPage } from './pages/hosts';
import { SystemPage } from './pages/system';
import { VpnsPage } from './pages/vpns';

export const routes: Routes = [
  { path: '', component: VpnsPage, title: 'VPNs' },
  { path: 'hosts', component: HostsPage, title: 'Host assignment' },
  { path: 'system', component: SystemPage, title: 'System status' },
  { path: '**', redirectTo: '' },
];
