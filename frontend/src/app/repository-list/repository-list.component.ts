import { Component, OnInit, OnDestroy } from '@angular/core';
import { Repository } from '../repository';
import { RepositoryService } from '../repository.service';
import { Subject, Subscription, timer, merge } from 'rxjs';
import { switchMap, takeUntil } from 'rxjs/operators';
import {
  ClarityIcons,
  trashIcon,
  hourglassIcon,
  syncIcon,
  checkCircleIcon,
  exclamationTriangleIcon,
  plusIcon,
  calendarIcon,
  popOutIcon,
  alarmClockIcon,
} from '@cds/core/icon';
import '@cds/core/icon/register.js';
ClarityIcons.addIcons(trashIcon, hourglassIcon, syncIcon, checkCircleIcon, exclamationTriangleIcon, plusIcon, calendarIcon, popOutIcon, alarmClockIcon);

@Component({
  selector: 'app-repository-list',
  standalone: false,
  templateUrl: './repository-list.component.html',
  styleUrls: ['./repository-list.component.css']
})
export class RepositoryListComponent implements OnInit, OnDestroy {
  repositories: Repository[] = [];
  selected: Repository[] = [];

  private destroy$ = new Subject<void>();
  private forceUpdate$ = new Subject<void>();

  // For modals
  isDeleteModalOpen = false;
  isAddModalOpen = false;
  isUpdateExpirationModalOpen = false;
  isAutoSyncModalOpen = false;
  isLogsModalOpen = false;
  currentLogs: string = 'Loading logs...';
  openGrokBaseUrl: string | null = null;
  autoSyncSettings = { enabled: false, schedule: '00:00' };
  newRetentionDays: number = 21; // Default value for the update modal
  toast: { message: string, type: string } = { message: '', type: 'info' };

  constructor(private repositoryService: RepositoryService) { }

  ngOnInit(): void {
    this.repositoryService.getConfig().subscribe(config => {
      this.openGrokBaseUrl = config.opengrok_base_url || null;
    });

    const polling$ = timer(0, 5000); // Poll every 5 seconds

    merge(polling$, this.forceUpdate$)
      .pipe(
        switchMap(() => this.repositoryService.getRepositories()),
        takeUntil(this.destroy$) // Unsubscribe when component is destroyed
      )
      .subscribe(repositories => {
        // Preserve selection across refreshes
        const selectedIds = new Set(this.selected.map(repo => repo.id));
        const newSelected: Repository[] = [];

        this.repositories = repositories;

        this.repositories.forEach(repo => {
          if (selectedIds.has(repo.id)) {
            newSelected.push(repo);
          }
        });
        this.selected = newSelected;
      });
  }

  private refreshList(): void {
    this.forceUpdate$.next();
  }

  private showToast(message: string, type: 'info' | 'success' | 'warning' | 'danger' = 'success'): void {
    this.toast = { message, type };
    // Automatically hide after 5 seconds
    setTimeout(() => {
      // Only clear if the message is still the same, to avoid clearing a newer toast
      if (this.toast.message === message) {
        this.closeToast();
      }
    }, 5000);
  }

  closeToast(): void {
    this.toast.message = '';
  }

  openBatchDeleteConfirm(): void {
    this.isDeleteModalOpen = true;
  }

  openUpdateExpirationModal(): void {
    this.isUpdateExpirationModalOpen = true;
  }

  openAutoSyncModal(): void {
    if (this.selected && this.selected.length > 0) {
      // Pre-fill with the settings of the first selected repository
      const firstSelected = this.selected[0];
      this.autoSyncSettings.enabled = firstSelected.auto_sync_enabled;
      this.autoSyncSettings.schedule = firstSelected.auto_sync_schedule || '00:00';
    }
    this.isAutoSyncModalOpen = true;
  }


  openLogsModal(repo: Repository): void {
    this.isLogsModalOpen = true;
    this.currentLogs = 'Loading logs...'; // Reset logs view
    this.repositoryService.getRepositoryLogs(repo.id).subscribe(
      response => this.currentLogs = response.logs,
      error => this.currentLogs = `Failed to load logs: ${error.message}`
    );
  }

  onBatchDelete(): void {
    if (this.selected && this.selected.length > 0) {
      this.repositoryService.deleteRepositories(this.selected).subscribe(() => {
        this.refreshList(); // Trigger an immediate refresh
        this.selected = []; // Clear selection
        this.isDeleteModalOpen = false; // Close modal
      });
    }
  }

  onUpdateExpiration(): void {
    // For debugging: Check if the method is called
    console.log('onUpdateExpiration triggered. Selected items:', this.selected);

    if (this.selected && this.selected.length > 0) {
      const retention = Number(this.newRetentionDays);
      this.repositoryService.updateRepositoriesExpiration(this.selected, retention).subscribe(() => {
        const repoCount = this.selected.length;
        this.refreshList(); // Trigger an immediate refresh
        this.selected = []; // Clear selection
        this.isUpdateExpirationModalOpen = false; // Close modal
        this.showToast(`Lease has been renewed for ${repoCount} repositories.`, 'success');
      });
    }
  }

  onUpdateAutoSync(): void {
    if (this.selected && this.selected.length > 0) {
      const repoCount = this.selected.length;
      const { enabled, schedule } = this.autoSyncSettings;
      this.repositoryService.updateRepositoriesAutoSync(this.selected, enabled, schedule).subscribe(() => {
        this.refreshList();
        this.selected = [];
        this.isAutoSyncModalOpen = false;
        this.showToast(`Auto-sync settings updated for ${repoCount} repositories.`, 'success');
      });
    }
  }

  onSyncSelected(): void {
    if (this.selected && this.selected.length > 0) {
      const repoCount = this.selected.length;
      this.repositoryService.syncRepositories(this.selected).subscribe(() => {
        // The status of the repositories will change to PENDING/RUNNING.
        // An immediate refresh will show this change to the user.
        this.refreshList();
        const repoNoun = repoCount === 1 ? 'repository' : 'repositories';
        this.showToast(`Sync initiated for ${repoCount} ${repoNoun}.`, 'success');
      });
    }
  }

  onRepositoryAdded(): void {
    this.refreshList(); // Trigger an immediate refresh to show the new repository
    this.isAddModalOpen = false;
  }

  trackById(index: number, item: Repository): number {
    return item.id;
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }
}
