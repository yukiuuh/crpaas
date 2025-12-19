import { ComponentFixture, TestBed } from '@angular/core/testing';
import { OpengrokStatusComponent } from './opengrok-status.component';
import { HttpClientTestingModule } from '@angular/common/http/testing';
import { ClarityModule } from '@clr/angular';
import { BytesPipe } from '../bytes.pipe';
import { of } from 'rxjs';
import { RepositoryService } from '../repository.service';

describe('OpengrokStatusComponent', () => {
  let component: OpengrokStatusComponent;
  let fixture: ComponentFixture<OpengrokStatusComponent>;

  beforeEach(async () => {
    const spy = jasmine.createSpyObj('RepositoryService', ['getOpenGrokStatus']);
    spy.getOpenGrokStatus.and.returnValue(of({ deployment_status: null, pod_statuses: [] }));

    await TestBed.configureTestingModule({
      declarations: [OpengrokStatusComponent],
      imports: [HttpClientTestingModule, ClarityModule, BytesPipe],
      providers: [
        { provide: RepositoryService, useValue: spy }
      ]
    })
      .compileComponents();

    fixture = TestBed.createComponent(OpengrokStatusComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
