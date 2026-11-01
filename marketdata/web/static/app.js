/* marketdata web UI (E15): draws the company page's history chart with the vendored Chart.js.
   It fetches nothing: the chart reads the JSON block its figure names, where each value is
   given exactly, as text, with its proof page. A bar opens its value's proof page. Without
   scripts the figure stays hidden and the table below it shows every value. */
(function () {
  "use strict";

  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function dataset(series) {
    return {
      label: series.name + " (" + series.unit + ")",
      data: series.values.map(function (value) {
        return value === null ? null : Number(value);
      }),
      backgroundColor: token("--accent"),
      borderRadius: 3
    };
  }

  function describe(series) {
    return series.name + " by quarter, " + series.unit + "; the values are in the table below";
  }

  function draw(figure) {
    var source = document.getElementById(figure.getAttribute("data-source"));
    if (typeof Chart === "undefined" || !source) {
      return;
    }
    var data = JSON.parse(source.textContent);
    if (!data.series.length) {
      return;
    }
    var select = figure.querySelector("select");
    var canvas = figure.querySelector("canvas");
    var current = data.series[0];
    figure.hidden = false;
    Chart.defaults.color = token("--muted");
    var chart = new Chart(canvas, {
      type: "bar",
      data: { labels: data.labels, datasets: [dataset(current)] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (item) {
                return current.text[item.dataIndex] + " " + current.unit;
              }
            }
          }
        },
        scales: {
          x: { grid: { display: false } },
          y: { grid: { color: token("--line") } }
        },
        onClick: function (event, elements) {
          var url = elements.length ? current.proof[elements[0].index] : null;
          if (url) {
            window.location.assign(url);
          }
        },
        onHover: function (event, elements) {
          canvas.style.cursor = elements.length ? "pointer" : "default";
        }
      }
    });
    canvas.setAttribute("aria-label", describe(current));
    select.addEventListener("change", function () {
      current = data.series[Number(select.value)];
      chart.data.datasets[0] = dataset(current);
      chart.update();
      canvas.setAttribute("aria-label", describe(current));
    });
  }

  document.querySelectorAll("figure[data-chart]").forEach(draw);
})();
