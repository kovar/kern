/**
 * ChartManager — wraps Chart.js with time-axis, date adapter, and time-window pruning.
 */
export class ChartManager {
  #chart = null;
  #data = [];         // shared reference — never reassigned
  #timeWindow = 300;  // seconds

  constructor(canvas) {
    const ctx = canvas.getContext('2d');
    this.#chart = new Chart(ctx, {
      type: 'line',
      data: {
        datasets: [{
          label: 'Weight',
          data: this.#data,
          borderColor: getComputedStyle(document.documentElement)
            .getPropertyValue('--chart-line').trim() || '#da1c29',
          backgroundColor: getComputedStyle(document.documentElement)
            .getPropertyValue('--chart-fill').trim() || 'rgba(218,28,41,0.08)',
          borderWidth: 2,
          pointRadius: 1,
          pointHoverRadius: 5,
          tension: 0.1,
          fill: true,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            type: 'time',
            time: {
              unit: 'second',
              displayFormats: { second: 'HH:mm:ss' },
            },
            title: { display: true, text: 'Time' },
          },
          y: {
            title: { display: true, text: 'Weight' },
            beginAtZero: false,
          },
        },
        plugins: {
          tooltip: {
            callbacks: {
              label: (ctx) => `Weight: ${ctx.parsed.y}`,
            },
          },
        },
        animation: false,
      },
    });
  }

  addReading(value) {
    if (typeof value !== 'number' || isNaN(value)) return;
    const now = new Date();
    this.#data.push({ x: now, y: value });
    this.#prune(now);
    this.#updateColors();
    this.#chart.update('none'); // skip animation
  }

  clear() {
    this.#data.length = 0; // preserve reference
    this.#chart.update();
  }

  setTimeWindow(seconds) {
    this.#timeWindow = seconds;
    this.#prune(new Date());
    this.#chart.update();
  }

  setYRange(min, max) {
    const yScale = this.#chart.options.scales.y;
    if (min !== null && min !== undefined && min !== '') {
      yScale.min = parseFloat(min);
    } else {
      delete yScale.min;
    }
    if (max !== null && max !== undefined && max !== '') {
      yScale.max = parseFloat(max);
    } else {
      delete yScale.max;
    }
    this.#chart.update();
  }

  resetZoom() {
    delete this.#chart.options.scales.y.min;
    delete this.#chart.options.scales.y.max;
    this.#chart.update();
  }

  destroy() {
    if (this.#chart) {
      this.#chart.destroy();
      this.#chart = null;
    }
  }

  #prune(now) {
    const cutoff = now.getTime() - this.#timeWindow * 1000;
    while (this.#data.length > 0 && this.#data[0].x.getTime() < cutoff) {
      this.#data.shift();
    }
  }

  #updateColors() {
    const style = getComputedStyle(document.documentElement);
    const ds = this.#chart.data.datasets[0];
    ds.borderColor = style.getPropertyValue('--chart-line').trim() || '#da1c29';
    ds.backgroundColor = style.getPropertyValue('--chart-fill').trim() || 'rgba(218,28,41,0.08)';
  }
}
