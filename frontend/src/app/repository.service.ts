import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, forkJoin, of, shareReplay } from 'rxjs';
import { Repository } from './repository';
import { RepositoryRequest } from "./repository-request";
import { OpenGrokStatusResponse } from './opengrok-status';

@Injectable({
  providedIn: 'root'
})
export class RepositoryService {

  // The backend API prefix is defined in main.py as /api/v1
  private repositoriesUrl = '/api/v1/repositories';
  private config$!: Observable<{ opengrok_base_url?: string }>;

  constructor(private http: HttpClient) { }

  getRepositories(): Observable<Repository[]> {
    // Angular's development server will proxy this request to the backend
    // based on the configuration in proxy.conf.json.
    return this.http.get<Repository[]>(this.repositoriesUrl);
  }

  getConfig(): Observable<{ opengrok_base_url?: string }> {
    if (!this.config$) {
      this.config$ = this.http.get<{ opengrok_base_url?: string }>('/api/v1/config').pipe(shareReplay(1));
    }
    return this.config$;
  }

  addRepository(repository: RepositoryRequest): Observable<Repository> {
    // The endpoint for adding a repository is /api/v1/repository
    return this.http.post<Repository>(`/api/v1/repository`, repository);
  }

  getRepositoryLogs(id: number): Observable<{ logs: string }> {
    const url = `/api/v1/repository/${id}/logs`;
    return this.http.get<{ logs: string }>(url);
  }

  getOpenGrokStatus(): Observable<OpenGrokStatusResponse> {
    return this.http.get<OpenGrokStatusResponse>('/api/v1/opengrok/status');
  }

  getOpenGrokLogs(podName: string, tailLines: number = 500): Observable<{ logs: string }> {
    return this.http.get<{ logs: string }>(`/api/v1/opengrok/logs?pod_name=${podName}&tail_lines=${tailLines}`);
  }

  updateRepositoryExpiration(id: number, retention_days: number): Observable<Repository> {
    const url = `/api/v1/repository/${id}/expiration`;
    return this.http.put<Repository>(url, { retention_days });
  }

  updateRepositoriesExpiration(repositories: Repository[], retention_days: number): Observable<Repository[]> {
    if (!repositories || repositories.length === 0) {
      return of([]);
    }
    const updateObservables = repositories.map(repo => this.updateRepositoryExpiration(repo.id, retention_days));
    return forkJoin(updateObservables);
  }

  deleteRepository(id: number): Observable<any> {
    const url = `/api/v1/repository/${id}`;
    return this.http.delete(url);
  }

  deleteRepositories(repositories: Repository[]): Observable<any[]> {
    if (!repositories || repositories.length === 0) {
      return of([]);
    }
    const deleteRequests = repositories.map(repo => this.deleteRepository(repo.id));
    return forkJoin(deleteRequests);
  }

  syncRepository(id: number): Observable<any> {
    const url = `/api/v1/repository/${id}/sync`;
    // An empty body is sent as this is a command endpoint.
    return this.http.post(url, {});
  }

  syncRepositories(repositories: Repository[]): Observable<any[]> {
    if (!repositories || repositories.length === 0) {
      return of([]);
    }
    const syncObservables = repositories.map(repo => this.syncRepository(repo.id));
    return forkJoin(syncObservables);
  }
  updateRepositoriesAutoSync(repositories: Repository[], enabled: boolean, schedule: string | null): Observable<Repository[]> {
    if (!repositories || repositories.length === 0) {
      return of([]);
    }

    const updatePayload = {
      auto_sync_enabled: enabled,
      auto_sync_schedule: enabled ? schedule : null
    };

    const updateObservables = repositories.map(repo =>
      this.http.put<Repository>(`/api/v1/repository/${repo.id}/autosync`, updatePayload));
    return forkJoin(updateObservables);
  }

  exportRepositories(): Observable<any> {
    return this.http.get('/api/v1/repositories/export');
  }

  importRepositories(data: { repositories: any[] }): Observable<any> {
    return this.http.post('/api/v1/repositories/import', data);
  }
}