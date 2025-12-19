import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpClientTestingModule } from '@angular/common/http/testing';
import { RepositoryListComponent } from './repository-list.component';
import { ClarityModule } from '@clr/angular';
import { RepositoryService } from '../repository.service';
import { of } from 'rxjs';
import { FormsModule } from '@angular/forms';

import { NO_ERRORS_SCHEMA } from '@angular/core';

describe('RepositoryListComponent', () => {
  let component: RepositoryListComponent;
  let fixture: ComponentFixture<RepositoryListComponent>;
  let repositoryServiceSpy: jasmine.SpyObj<RepositoryService>;

  beforeEach(async () => {
    repositoryServiceSpy = jasmine.createSpyObj('RepositoryService', [
      'getRepositories', 'getConfig', 'syncRepositories', 'deleteRepositories',
      'updateRepositoriesExpiration', 'updateRepositoriesAutoSync', 'exportRepositories',
      'importRepositories', 'getRepositoryLogs', 'getOpenGrokStatus'
    ]);
    repositoryServiceSpy.getRepositories.and.returnValue(of([]));
    repositoryServiceSpy.getConfig.and.returnValue(of({ opengrok_base_url: 'http://test' }));
    repositoryServiceSpy.getOpenGrokStatus.and.returnValue(of({ deployment_status: null, pod_statuses: [] }));

    await TestBed.configureTestingModule({
      declarations: [RepositoryListComponent],
      imports: [HttpClientTestingModule, ClarityModule, FormsModule],
      providers: [
        { provide: RepositoryService, useValue: repositoryServiceSpy }
      ],
      schemas: [NO_ERRORS_SCHEMA]
    })
      .compileComponents();

    fixture = TestBed.createComponent(RepositoryListComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
