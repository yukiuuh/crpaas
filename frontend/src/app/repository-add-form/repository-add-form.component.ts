import { Component, EventEmitter, Output } from '@angular/core';
import { RepositoryService } from '../repository.service';
import { RepositoryRequest } from '../repository-request';

@Component({
  selector: 'app-repository-add-form',
  standalone: false,
  templateUrl: './repository-add-form.component.html',
  styleUrls: ['./repository-add-form.component.css']
})
export class RepositoryAddFormComponent {
  model: RepositoryRequest = {
    repo_url: '',
    commit_id: '',
    project_name: '',
    retention_days: 21, // Default retention period in days
    clone_single_branch: false,
    clone_recursive: false,
    auto_sync_enabled: false,
    auto_sync_schedule: '00:00'
  };

  @Output() repositoryAdded = new EventEmitter<void>();
  @Output() cancel = new EventEmitter<void>();

  constructor(private repositoryService: RepositoryService) { }

  onSubmit() {
    // project_nameが空文字列の場合は送信しない
    const request: RepositoryRequest = { ...this.model };
    if (!request.project_name) {
      delete request.project_name;
    }
    // Ensure retention_days is a number
    request.retention_days = Number(request.retention_days);
    if (isNaN(request.retention_days)) {
      request.retention_days = 21; // Fallback to default if input is invalid
    }

    if (!request.auto_sync_enabled) {
      delete request.auto_sync_schedule;
    }

    this.repositoryService.addRepository(request).subscribe(() => {
      this.repositoryAdded.emit();
    });
  }

  onCancel() {
    this.cancel.emit();
  }
}