import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';
import { OpengrokStatusComponent } from './opengrok-status/opengrok-status.component';
import { RepositoryListComponent } from './repository-list/repository-list.component';

const routes: Routes = [
  { path: '', component: RepositoryListComponent },
  { path: 'status', component: OpengrokStatusComponent },
];

@NgModule({
  imports: [RouterModule.forRoot(routes)],
  exports: [RouterModule]
})
export class AppRoutingModule { }
