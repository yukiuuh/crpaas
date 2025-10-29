import { Component, OnInit } from '@angular/core';
import { Observable } from 'rxjs';
import { OpenGrokPodStatus } from '../opengrok-status';
import { RepositoryService } from '../repository.service';

@Component({
  selector: 'app-opengrok-status',
  standalone: false,
  templateUrl: './opengrok-status.component.html',
  styleUrl: './opengrok-status.component.css'
})
export class OpengrokStatusComponent implements OnInit {
  public status$!: Observable<OpenGrokPodStatus[]>;
  public logModalOpen = false;
  public selectedPodName: string | null = null;
  public selectedPodLogs$: Observable<{ logs: string }> | null = null;

  constructor(private repositoryService: RepositoryService) { }

  ngOnInit(): void {
    this.status$ = this.repositoryService.getOpenGrokStatus();
  }

  public getUsePercentValue(usePercentStr: string | undefined | null): number {
    if (!usePercentStr) {
      return 0;
    }
    return parseFloat(usePercentStr);
  }

  public showOpenGrokLogs(podName: string): void {
    this.selectedPodName = podName;
    this.selectedPodLogs$ = this.repositoryService.getOpenGrokLogs(podName);
    this.logModalOpen = true;
  }
}
