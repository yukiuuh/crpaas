import { Component, OnInit } from '@angular/core';
import { Observable } from 'rxjs';
import { OpenGrokPodStatus, OpenGrokStatusResponse } from '../opengrok-status';
import { ClrDatagridComparatorInterface } from '@clr/angular';
import { RepositoryService } from '../repository.service';

@Component({
  selector: 'app-opengrok-status',
  standalone: false,
  templateUrl: './opengrok-status.component.html',
  styleUrl: './opengrok-status.component.css'
})
export class OpengrokStatusComponent implements OnInit {
  public statusResponse$!: Observable<OpenGrokStatusResponse>;
  public logModalOpen = false;
  public selectedPodName: string | null = null;
  public selectedPodLogs$: Observable<{ logs: string }> | null = null;

  public cpuUsageComparator: ClrDatagridComparatorInterface<OpenGrokPodStatus> = {
    compare: (a: OpenGrokPodStatus, b: OpenGrokPodStatus) => {
      const cpuA = this.parseCpuUsage(a.cpu_usage);
      const cpuB = this.parseCpuUsage(b.cpu_usage);
      return cpuA - cpuB;
    }
  };

  public memoryUsageComparator: ClrDatagridComparatorInterface<OpenGrokPodStatus> = {
    compare: (a: OpenGrokPodStatus, b: OpenGrokPodStatus) => {
      const memA = this.parseMemoryUsage(a.memory_usage);
      const memB = this.parseMemoryUsage(b.memory_usage);
      return memA - memB;
    }
  };


  constructor(private repositoryService: RepositoryService) { }

  ngOnInit(): void {
    this.statusResponse$ = this.repositoryService.getOpenGrokStatus();
  }

  public getUsePercentValue(usePercentStr: string | undefined | null): number {
    if (!usePercentStr) {
      return 0;
    }
    return parseFloat(usePercentStr);
  }

  public parseCpuUsage(cpuUsage: string | undefined | null): number {
    if (!cpuUsage) {
      return 0;
    }

    const value = parseFloat(cpuUsage);

    // Handles '123n' (nanocores) -> 1m = 1,000,000n
    if (cpuUsage.endsWith('n')) {
      return value / 1000000;
    }
    // Handles '123u' (microcores) -> 1m = 1,000u
    if (cpuUsage.endsWith('u')) {
      return value / 1000;
    }
    // Handles '123m' (millicores)
    if (cpuUsage.endsWith('m')) {
      return value;
    }
    // Handles '1.5' or '1' (cores)
    return value * 1000; // Convert cores to millicores
  }

  public parseMemoryUsage(memoryUsage: string | undefined | null): number {
    if (!memoryUsage) {
      return 0;
    }
    // Handles '12345Ki', '123Mi', etc.
    const value = parseFloat(memoryUsage);
    if (memoryUsage.endsWith('Ki')) {
      return value;
    }
    if (memoryUsage.endsWith('Mi')) {
      return value * 1024;
    }
    if (memoryUsage.endsWith('Gi')) {
      return value * 1024 * 1024;
    }
    return value / 1024; // Assume bytes if no unit, convert to Ki
  }

  public showOpenGrokLogs(podName: string): void {
    this.selectedPodName = podName;
    this.selectedPodLogs$ = this.repositoryService.getOpenGrokLogs(podName);
    this.logModalOpen = true;
  }
}
