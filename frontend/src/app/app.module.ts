import { NgModule } from "@angular/core";
import { BrowserModule } from "@angular/platform-browser";
import { BrowserAnimationsModule } from "@angular/platform-browser/animations";
import { ClarityModule } from "@clr/angular";
import { HttpClientModule } from "@angular/common/http";
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';

import { AppRoutingModule } from './app-routing.module';
import { AppComponent } from './app.component';
import { RepositoryListComponent } from './repository-list/repository-list.component';
import { RepositoryAddFormComponent } from './repository-add-form/repository-add-form.component';
import { OpengrokStatusComponent } from './opengrok-status/opengrok-status.component';
import { BytesPipe } from './bytes.pipe';

@NgModule({
  declarations: [
    AppComponent,
    RepositoryListComponent,
    RepositoryAddFormComponent,
    OpengrokStatusComponent
  ],
  imports: [
    BrowserModule,
    BrowserAnimationsModule,
    ClarityModule,
    AppRoutingModule,
    HttpClientModule,
    FormsModule,
    CommonModule,
    BytesPipe
  ],
  providers: [],
  bootstrap: [AppComponent]
})
export class AppModule { }
