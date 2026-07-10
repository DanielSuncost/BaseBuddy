(function () {
  if (typeof Chart === 'undefined') return;

  var canvas = document.getElementById('activityChart');
  if (canvas && typeof hourlyStats !== 'undefined') {
    var labels = (hourlyStats || []).map(function (h) {
      return h.hour != null ? h.hour + ':00' : (h.label || '');
    });
    var data = (hourlyStats || []).map(function (h) {
      return h.count != null ? h.count : (h.detections || 0);
    });
    new Chart(canvas, {
      type: 'line',
      data: {
        labels: labels.length ? labels : ['—'],
        datasets: [{
          label: 'Detections',
          data: data.length ? data : [0],
          borderColor: '#1a73e8',
          backgroundColor: 'rgba(26,115,232,0.1)',
          fill: true,
          tension: 0.3,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }

  setInterval(function () {
    location.reload();
  }, 120000);
})();
