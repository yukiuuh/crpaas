import { ComponentFixture, TestBed } from '@angular/core/testing';

import { OpengrokStatusComponent } from './opengrok-status.component';

describe('OpengrokStatusComponent', () => {
  let component: OpengrokStatusComponent;
  let fixture: ComponentFixture<OpengrokStatusComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      declarations: [OpengrokStatusComponent]
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
