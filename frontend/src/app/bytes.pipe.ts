import { Pipe, PipeTransform } from '@angular/core';

@Pipe({
  name: 'bytes',
  standalone: true
})
export class BytesPipe implements PipeTransform {

  transform(value: number | null | undefined, precision = 2): string {
    if (value === null || value === undefined || isNaN(value) || value === 0) {
      return '0 KB';
    }

    const units = ['KB', 'MB', 'GB', 'TB'];
    let unitIndex = 0;
    let transformedValue = value;

    while (transformedValue >= 1024 && unitIndex < units.length - 1) {
      transformedValue /= 1024;
      unitIndex++;
    }

    return `${transformedValue.toFixed(precision)} ${units[unitIndex]}`;
  }
}
