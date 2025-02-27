"""Supporting code for quickchart functionality."""

import inspect
import textwrap
import uuid as _uuid

from google.colab import _quickchart_lib
import IPython.display
import numpy as np


def _chunked(seq, chunk_size):
  """Partitions the sequence into equally-sized slices.

  If the sequence length is not evenly divisible by the chunk size, the
  remainder is included rather than truncated.

  Args:
    seq: (iterable) A sequence.
    chunk_size: (int) The size of each sequence partition.

  Yields:
    (sequence<sequence<T>>) A sequence of chunks.
  """
  # Lazy import to avoid loading on kernel init.
  # TODO(b/275732775): switch back to itertools.pairwise when possible.
  import more_itertools  # pylint: disable=g-import-not-at-top

  for start, end in more_itertools.pairwise(
      list(range(0, len(seq), chunk_size)) + [len(seq)]
  ):
    yield seq[start:end]


class ChartSection:
  """Grouping of charts and other displayable objects."""

  def __init__(self, charts, displayables):
    self._charts = charts
    self._displayables = displayables

  @property
  def charts(self):
    return self._charts

  def display(self):
    for d in self._displayables:
      d.display()


class SectionTitle:
  """Section title used for delineating chart sections."""

  def __init__(self, title):
    self.title = title

  def display(self):
    IPython.display.display(self)

  def _repr_html_(self):
    return f'<h4 class="colab-quickchart-section-title">{self.title}</h4>'


class DataframeRegistry:
  """Dataframe registry for charts-with-code that may be displayed."""

  def __init__(self):
    self._df_chart_registry = {}

  def register_df_varname(self, df):
    """Registers a given dataframe.

    Equivalent dataframes (as determined by hash value) will receive the same
    name on repeated requests.

    Args:
      df: (pd.DataFrame) A dataframe.

    Returns:
      (str) A unique^* variable name for the dataframe.
      (^* modulo unlikely hash collisions)
    """
    df_name = f'df_{abs(hash(df.values.tobytes()))}'
    self._df_chart_registry[df_name] = df
    return df_name

  def __getitem__(self, df_varname):
    return self._df_chart_registry[df_varname]


class ChartWithCode:
  """Wrapper for chart that also knows how to get its own code."""

  def __init__(self, df, plot_func, args, kwargs, df_registry):
    self._df = df
    self._df_registry = df_registry
    self._df_varname = self._df_registry.register_df_varname(df)

    self._plot_func = plot_func
    self._args = args
    self._kwargs = kwargs

    self._chart_id = f'chart-{str(_uuid.uuid4())}'
    self._chart = plot_func(df, *args, **kwargs)

  @property
  def chart_id(self):
    return self._chart_id

  def display(self):
    """Displays the chart within a notebook context."""
    IPython.display.display(self)

  def get_code(self):
    """Gets the code and associated dependencies + context for a given chart."""

    plot_func_src = inspect.getsource(self._plot_func)
    plot_invocation = textwrap.dedent(
        """\
        chart = {plot_func}({df_varname}, *{args}, **{kwargs})
        chart""".format(
            plot_func=self._plot_func.__name__,
            args=str(self._args),
            kwargs=str(self._kwargs),
            df_varname=self._df_varname,
        )
    )

    chart_src = textwrap.dedent("""\
        import altair as alt
        from google.colab import _quickchart
        {df_varname} = _quickchart.get_registered_df('{df_varname}')
        """.format(df_varname=self._df_varname))
    chart_src += '\n'
    chart_src += plot_func_src
    chart_src += '\n'
    chart_src += plot_invocation
    return chart_src

  def _repr_html_(self):
    """Gets the HTML representation of the chart."""

    chart_html = self._chart._repr_mimebundle_()['text/html']  # pylint:disable = protected-access
    script_start = chart_html.find('<script')
    return f"""\
      <div class="colab-quickchart-chart-with-code" id="{self._chart_id}">
        {chart_html[:script_start]}
      </div>
      {chart_html[script_start:]}
      <script type="text/javascript">
        (() => {{
          const chartElement = document.getElementById("{self._chart_id}");
          async function getCodeForChartHandler(event) {{
            const chartCodeResponse =  await google.colab.kernel.invokeFunction(
                'getCodeForChart', ["{self._chart_id}"], {{}});
            const responseJson = chartCodeResponse.data['application/json'];
            await google.colab.notebook.addCell(responseJson.code, 'code');
          }}
          chartElement.onclick = getCodeForChartHandler;
        }})();
      </script>
      <style>
        .colab-quickchart-chart-with-code  {{
            display: block;
            float: left;
            border: 1px solid transparent;
        }}

        .colab-quickchart-chart-with-code:hover {{
            cursor: pointer;
            border: 1px solid #aaa;
        }}
      </style>"""

  def __repr__(self):
    return self.get_code()


def histograms_section(df, colnames, df_registry):
  """Generates a section of histograms.

  Args:
    df: (pd.DataFrame) A dataframe.
    colnames: (iterable<str>) The column names for which to generate plots.
    df_registry: (DataframeRegistry) Registry to use for dataframe lookups.

  Returns:
    (ChartSection) A chart section containing histograms.
  """
  return _chart_section(
      df, _quickchart_lib.histogram, colnames, {}, df_registry, 'Distributions'
  )


def value_plots_section(df, colnames, df_registry):
  """Generates a section of value plots.

  Args:
    df: (pd.DataFrame) A dataframe.
    colnames: (iterable<str>) The column names for which to generate plots.
    df_registry: (DataframeRegistry) Registry to use for dataframe lookups.

  Returns:
    (ChartSection) A chart section containing value plots.
  """
  return _chart_section(
      df, _quickchart_lib.value_plot, colnames, {}, df_registry, 'Values'
  )


def categorical_histograms_section(df, colnames, df_registry):
  """Generates a section of categorical histograms.

  Args:
    df: (pd.DataFrame) A dataframe.
    colnames: (iterable<str>) The column names for which to generate histograms.
    df_registry: (DataframeRegistry) Registry to use for dataframe lookups.

  Returns:
    (ChartSection) A chart section containing categorical histograms.
  """
  return _chart_section(
      df,
      _quickchart_lib.categorical_histogram,
      colnames,
      {},
      df_registry,
      'Categorical distributions',
  )


def heatmaps_section(df, colname_pairs, df_registry):
  """Generates a section of heatmaps.

  Args:
    df: (pd.DataFrame) A dataframe.
    colname_pairs: (iterable<str, str>) Sequence of (x-axis, y-axis) column name
      pairs to plot.
    df_registry: (DatframeRegistry) Registry to use for dataframe lookups.

  Returns:
    (ChartSection) A chart section containing heatmaps.
  """
  return _chart_section(
      df, _quickchart_lib.heatmap, colname_pairs, {}, df_registry, 'Heatmaps'
  )


def linked_scatter_section(df, colname_pairs, df_registry):
  """Generates a section of linked scatter plots.

  Args:
    df: (pd.DataFrame) A dataframe.
    colname_pairs: (iterable<str, str>) Sequence of (x-colname, y-colname) pairs
      to plot.
    df_registry: (DataframeRegistry) Registry to use for dataframe lookups.

  Returns:
    (ChartSection) A chart section containing linked scatter plots.
  """
  return _chart_section(
      df,
      _quickchart_lib.linked_scatter_plots,
      [[list(colname_pairs)]],
      {},
      df_registry,
      '2-d distributions',
  )


def swarm_plots_section(df, colname_pairs, df_registry):
  """Generates a section of swarm plots.

  Args:
    df: (pd.DataFrame) A dataframe.
    colname_pairs: (iterable<str, str>) Sequence of (value, facet) column name
      pairs to plot.
    df_registry: (DataframeRegistry) Registry to use for dataframe lookups.

  Returns:
    (ChartSection) A chart section containing swarm plots.
  """
  return _chart_section(
      df,
      _quickchart_lib.swarm_plot,
      colname_pairs,
      {},
      df_registry,
      'Swarm plots',
  )


def _chart_section(df, plot_func, args_per_chart, kwargs, df_registry, title):
  """Generates a chart section.

  Args:
    df: (pd.DataFrame) A dataframe.
    plot_func: (Function) Rendering function mapping (df, *args, **kwargs) =>
      <IPython displayble>
    args_per_chart: (iterable<args>) Sequence of arguments to pass for each
      chart in the section.
    kwargs: (dict) Common set of keyword args to pass for each chart.
    df_registry: (DataframeRegistry) Registry to use for dataframe lookups.
    title: (str) Section title to display.

  Returns:
    (ChartSection) A chart section.
  """
  charts = [
      ChartWithCode(
          df, plot_func, np.atleast_1d(args).tolist(), kwargs, df_registry
      )
      for args in args_per_chart
  ]
  return ChartSection(
      charts=charts, displayables=([SectionTitle(title)] + charts)
  )
