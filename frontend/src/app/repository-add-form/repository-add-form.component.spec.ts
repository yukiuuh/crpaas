import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpClientTestingModule } from '@angular/common/http/testing';
import { FormsModule } from '@angular/forms';
import { ClarityModule } from '@clr/angular';
import { RepositoryAddFormComponent } from './repository-add-form.component';
import { RepositoryService } from '../repository.service';

describe('RepositoryAddFormComponent', () => {
    let component: RepositoryAddFormComponent;
    let fixture: ComponentFixture<RepositoryAddFormComponent>;

    beforeEach(async () => {
        await TestBed.configureTestingModule({
            declarations: [RepositoryAddFormComponent],
            imports: [HttpClientTestingModule, FormsModule, ClarityModule],
            providers: [RepositoryService]
        })
            .compileComponents();

        fixture = TestBed.createComponent(RepositoryAddFormComponent);
        component = fixture.componentInstance;
        fixture.detectChanges();
    });

    it('should create', () => {
        expect(component).toBeTruthy();
    });
});
