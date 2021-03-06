#!/usr/bin/env python
# -*- coding: utf-8 -*-

# models.plot.py
"""
Classes to generate bokeh plots
"""
# Copyright (c) 2016-2019 Dan Cutright
# This file is part of DVH Analytics, released under a BSD license.
#    See the file LICENSE included with this distribution, also
#    available at https://github.com/cutright/DVH-Analytics

import wx.html2
from bokeh.plotting import figure
from bokeh.io.export import get_layout_html
from bokeh.models import Legend, HoverTool, ColumnDataSource, DataTable, TableColumn,\
    NumberFormatter, Div, Range1d, LabelSet, Spacer
from bokeh.layouts import column, row
from bokeh.palettes import Colorblind8 as palette
import itertools
import numpy as np
from math import pi
from os.path import join, isdir
from os import mkdir
from dvha.tools.utilities import collapse_into_single_dates, moving_avg, is_windows
from dvha.tools.stats import MultiVariableRegression, get_control_limits
from dvha.paths import TEMP_DIR


DEFAULT_TOOLS = "pan,box_zoom,crosshair,reset"


# TODO: have all plot classes load options with a function that runs on update_plot to get latest options
class Plot:
    """
    Base class for all other plots
    Pass the layout property into a wx sizer
    """
    def __init__(self, parent, options, x_axis_label='X Axis', y_axis_label='Y Axis', x_axis_type='linear',
                 tools=DEFAULT_TOOLS):
        """
        :param parent: the wx UI object where the plot will be displayed
        :param options: user options object for visual preferences
        :type options: Options
        :param x_axis_label: text for the x-axis title
        :type x_axis_label: str
        :param y_axis_label: text for the y-axis title
        :type y_axis_label: str
        :param x_axis_type: x axis type per bokeh (e.g., 'linear' or 'datetime')
        :type x_axis_type: str
        """

        self.options = options

        self.layout = wx.html2.WebView.New(parent)
        self.bokeh_layout = None
        self.html_str = ''

        # For windows users, since wx.html2 requires a file to load rather than passing a string
        # The file name for each plot will be join(TEMP_DIR, "%s.html" % self.type)
        self.type = None

        self.figure = figure(x_axis_type=x_axis_type, tools=tools, toolbar_sticky=True)
        self.figure.xaxis.axis_label = x_axis_label
        self.figure.yaxis.axis_label = y_axis_label

        self.source = {}  # Will be a dictionary of bokeh ColumnDataSources

        if self.options:
            self.__apply_default_figure_options()

    def __apply_default_figure_options(self):
        self.figure.xaxis.axis_label_text_font_size = self.options.PLOT_AXIS_LABEL_FONT_SIZE
        self.figure.yaxis.axis_label_text_font_size = self.options.PLOT_AXIS_LABEL_FONT_SIZE
        self.figure.xaxis.major_label_text_font_size = self.options.PLOT_AXIS_MAJOR_LABEL_FONT_SIZE
        self.figure.yaxis.major_label_text_font_size = self.options.PLOT_AXIS_MAJOR_LABEL_FONT_SIZE
        self.figure.min_border = self.options.MIN_BORDER
        self.figure.yaxis.axis_label_text_baseline = "bottom"

    def clear_plot(self):
        if self.bokeh_layout:
            self.clear_sources()
            self.figure.xaxis.axis_label = ''
            self.figure.yaxis.axis_label = ''
            self.update_bokeh_layout_in_wx_python()

    def clear_source(self, source_key):
        data = {data_key: [] for data_key in list(self.source[source_key].data)}
        self.source[source_key].data = data

    def clear_sources(self):
        for key in list(self.source):
            self.clear_source(key)

    def update_bokeh_layout_in_wx_python(self):
        self.html_str = get_layout_html(self.bokeh_layout)
        if is_windows():  # Windows requires LoadURL()
            if not isdir(TEMP_DIR):
                mkdir(TEMP_DIR)
            web_file = join(TEMP_DIR, "%s.html" % self.type)
            with open(web_file, 'wb') as f:
                f.write(self.html_str.encode("utf-8"))
            self.layout.LoadURL(web_file)
        else:
            self.layout.SetPage(self.html_str, "")

    @staticmethod
    def clean_data(*data, mrn=None, uid=None, dates=None):
        """
        Data used for statistical analysis in Regression and Control Charts requires no 'None' values and the same
        number of points for each variable.  To mitigate this, clean_data will find all studies that have any 'None'
        values and return data without these studies
        :param data: any number of variables, each being a list of values
        :param mrn: mrns in same order as data
        :param uid: study instance uids in same order data
        :param dates: sim study dates in same order as data
        :return: data only including studies with no 'None' values
        :rtype: tuple
        """
        bad_indices = []
        for var in data:
            bad_indices.extend([i for i, value in enumerate(var) if value == 'None'])
        bad_indices = set(bad_indices)

        ans = [[value for i, value in enumerate(var) if i not in bad_indices] for var in data]

        for var in [mrn, uid, dates]:
            if var:
                ans.append([value for i, value in enumerate(var) if i not in bad_indices])

        return tuple(ans)

    def set_figure_dimensions(self):
        pass

    def redraw_plot(self):
        self.set_figure_dimensions()
        self.update_bokeh_layout_in_wx_python()


class PlotStatDVH(Plot):
    """
    Generate plot for DVHs tab
    """
    def __init__(self, parent, dvh, options):
        """
        :param parent: the wx UI object where the plot will be displayed
        :param dvh: dvh data object
        :type dvh: DVH
        :param options: user preferences
        :type options: Options
        """
        Plot.__init__(self, parent, options, x_axis_label='Dose (cGy)', y_axis_label='Relative Volume')

        self.type = 'dvh'
        self.parent = parent
        self.size_factor = {'plot': (0.885, 0.522),
                            'table': (0.885, 0.359)}

        self.options = options
        self.dvh = dvh
        self.source = {'dvh': ColumnDataSource(data=dict(x=[], y=[], mrn=[], uid=[], roi_name=[], roi_type=[],
                                                         x_dose=[], volume=[], min_dose=[], mean_dose=[], max_dose=[])),
                       'stats': ColumnDataSource(data=dict(x=[], min=[], mean=[], median=[], max=[], mrn=[])),
                       'patch': ColumnDataSource(data=dict(x=[], y1=[], y2=[]))}
        self.layout_done = False
        self.stat_dvhs = {key: np.array(0) for key in ['min', 'q1', 'mean', 'median', 'q3', 'max']}
        self.x = []

        self.__add_hover()
        self.__add_plot_data()
        self.__add_legend()
        self.__create_table()

        self.bokeh_layout = column(self.figure, self.table)

    def __add_hover(self):
        # TODO: custom hover not behaving?
        # Display only one tool tip (since many lines will overlap)
        # https://stackoverflow.com/questions/36434562/displaying-only-one-tooltip-when-using-the-hovertool-tool?rq=1
        custom_hover = HoverTool()
        custom_hover.tooltips = """
                    <style>
                        .bk-tooltip>div:not(:first-child) {display:none;}
                    </style>

                    <b>MRN: </b> @mrn <br>
                    <b>Dose: </b> $x{i} cGy <br>
                    <b>Volume: </b> $y
                """
        self.figure.add_tools(custom_hover)

    def __add_plot_data(self):
        self.figure.multi_line('x', 'y', source=self.source['dvh'], selection_color='color',
                               line_width=self.options.DVH_LINE_WIDTH,
                               alpha=0, line_dash=self.options.DVH_LINE_DASH, nonselection_alpha=0, selection_alpha=1)

        # Add statistical plots to figure
        self.stats_max = self.figure.line('x', 'max', source=self.source['stats'],
                                          line_width=self.options.STATS_MAX_LINE_WIDTH, color=self.options.PLOT_COLOR,
                                          line_dash=self.options.STATS_MAX_LINE_DASH, alpha=self.options.STATS_MAX_ALPHA)
        self.stats_median = self.figure.line('x', 'median', source=self.source['stats'],
                                             line_width=self.options.STATS_MEDIAN_LINE_WIDTH,
                                             color=self.options.PLOT_COLOR, line_dash=self.options.STATS_MEDIAN_LINE_DASH,
                                             alpha=self.options.STATS_MEDIAN_ALPHA)
        self.stats_mean = self.figure.line('x', 'mean', source=self.source['stats'],
                                           line_width=self.options.STATS_MEAN_LINE_WIDTH,
                                           color=self.options.PLOT_COLOR, line_dash=self.options.STATS_MEAN_LINE_DASH,
                                           alpha=self.options.STATS_MEAN_ALPHA)
        self.stats_min = self.figure.line('x', 'min', source=self.source['stats'],
                                          line_width=self.options.STATS_MIN_LINE_WIDTH, color=self.options.PLOT_COLOR,
                                          line_dash=self.options.STATS_MIN_LINE_DASH, alpha=self.options.STATS_MIN_ALPHA)

        # Shaded region between Q1 and Q3
        self.iqr = self.figure.varea('x', 'y1', 'y2', source=self.source['patch'], alpha=self.options.IQR_ALPHA,
                                     color=self.options.PLOT_COLOR)

    def __add_legend(self):
        # Set the legend (for stat dvhs only)
        legend_stats = Legend(items=[("Max  ", [self.stats_max]),
                                     ("Median  ", [self.stats_median]),
                                     ("Mean  ", [self.stats_mean]),
                                     ("Min  ", [self.stats_min]),
                                     ("IQR  ", [self.iqr])],
                              orientation='horizontal')

        # Add the layout outside the plot, clicking legend item hides the line
        self.figure.add_layout(legend_stats, 'above')
        self.figure.legend.click_policy = "hide"

    def __create_table(self):
        columns = [TableColumn(field="mrn", title="MRN", width=175),
                   TableColumn(field="roi_name", title="ROI Name"),
                   TableColumn(field="roi_type", title="ROI Type", width=80),
                   TableColumn(field="rx_dose", title="Rx Dose", width=100, formatter=NumberFormatter(format="0.00")),
                   TableColumn(field="volume", title="Volume", width=80, formatter=NumberFormatter(format="0.00")),
                   TableColumn(field="min_dose", title="Min Dose", width=80, formatter=NumberFormatter(format="0.00")),
                   TableColumn(field="mean_dose", title="Mean Dose", width=80,
                               formatter=NumberFormatter(format="0.00")),
                   TableColumn(field="max_dose", title="Max Dose", width=80,
                               formatter=NumberFormatter(format="0.00")), ]
        self.table = DataTable(source=self.source['dvh'], columns=columns,)

    def set_figure_dimensions(self):
        panel_width, panel_height = self.parent.GetSize()

        self.figure.plot_width = int(self.size_factor['plot'][0] * float(panel_width))
        self.figure.plot_height = int(self.size_factor['plot'][1] * float(panel_height))
        self.table.width = int(self.size_factor['table'][0] * float(panel_width))
        self.table.height = int(self.size_factor['table'][1] * float(panel_height))

    def update_plot(self, dvh):

        self.set_figure_dimensions()

        self.clear_sources()
        self.dvh = dvh
        self.x = list(range(dvh.bin_count))
        self.stat_dvhs = dvh.get_standard_stat_dvh()

        data = {'dvh': dvh.get_cds_data(),
                'stats': {key: self.stat_dvhs[key] for key in ['max', 'median', 'mean', 'min']},
                'patch': {'x': self.x, 'y1': self.stat_dvhs['q3'], 'y2': self.stat_dvhs['q1']}}

        # Add additional data to dvh data
        data['dvh']['x'] = dvh.x_data
        data['dvh']['y'] = dvh.y_data
        data['dvh']['mrn'] = dvh.mrn
        data['dvh']['roi_name'] = dvh.roi_name
        data['dvh']['color'] = [color for j, color in zip(range(dvh.count), itertools.cycle(palette))]

        # Add x-axis to stats dvhs
        data['stats']['x'] = self.x

        # update bokeh CDS
        for key, obj in data.items():
            self.source[key].data = obj

        self.figure.xaxis.axis_label = 'Dose (cGy)'
        self.figure.yaxis.axis_label = 'Relative Volume'

        self.update_bokeh_layout_in_wx_python()

    def get_csv(self, include_summary=True, include_dvhs=True):
        """
        Get a csv string of DVH data used for data export
        :param include_summary: table of DVH related data, without histogram data
        :type include_summary: bool
        :param include_dvhs: table of histogram data
        :type include_dvhs: bool
        :return: data as a csv
        :rtype: str
        """
        data = self.source['dvh'].data
        summary, dvh_data = [], []

        if include_summary:
            summary = ['MRN,Study Instance UID,ROI Name,ROI Type,Rx Dose,Volume,Min Dose,Mean Dose,Max Dose']
            for i, mrn in enumerate(data['mrn']):
                keys = ['mrn', 'study_instance_uid', 'roi_name', 'roi_type', 'rx_dose',
                        'volume', 'min_dose', 'mean_dose', 'max_dose']
                summary.append(','.join([str(data[key][i]).replace(',', '^') for key in keys]))
            summary.append('')

        if include_dvhs:
            max_x = max([len(x) for x in data['x']])
            dvh_data = ['MRN,Study Instance UID,ROI Name,Dose bins (cGy) ->,%s' % ','.join([str(x) for x in range(max_x)])]
            for i, mrn in enumerate(data['mrn']):
                clean_mrn = mrn.replace(',', '^')
                clean_uid = data['study_instance_uid'][i].replace(',', '^')
                clean_roi = data['roi_name'][i].replace(',', '^')
                dvh_data.append("%s,%s,%s,,%s" %
                                (clean_mrn, clean_uid, clean_roi, ','.join(str(y) for y in data['y'][i])))

        return '\n'.join(summary + dvh_data)


class PlotTimeSeries(Plot):
    """
    Generate plot for Time Series tab
    """
    def __init__(self, parent, options):
        """
        :param parent: the wx UI object where the plot will be displayed
        :param options: user preferences
        :type options: Options
        """
        Plot.__init__(self, parent, options, x_axis_label='Simulation Date', x_axis_type='datetime')

        self.type = 'time_series'
        self.parent = parent
        self.size_factor = {'plot': (0.885, 0.424),
                            'hist': (0.885, 0.359)}

        self.options = options
        self.source = {'plot': ColumnDataSource(data=dict(x=[], y=[], mrn=[], uid=[])),
                       'hist': ColumnDataSource(data=dict(x=[], top=[], width=[])),
                       'trend': ColumnDataSource(data=dict(x=[], y=[], mrn=[])),
                       'bound': ColumnDataSource(data=dict(x=[], mrn=[], upper=[], avg=[], lower=[])),
                       'patch': ColumnDataSource(data=dict(x=[], y=[]))}
        self.y_axis_label = ''

        self.div = Div(text='<hr>')

        self.__add_plot_data()
        self.__add_histogram_data()
        self.__add_legend()
        self.__add_hover()
        self.__do_layout()

    def __add_plot_data(self):
        self.plot_data = self.figure.circle('x', 'y', source=self.source['plot'], size=self.options.TIME_SERIES_CIRCLE_SIZE,
                                            alpha=self.options.TIME_SERIES_CIRCLE_ALPHA, color=self.options.PLOT_COLOR)

        self.plot_trend = self.figure.line('x', 'y', color=self.options.PLOT_COLOR, source=self.source['trend'],
                                           line_width=self.options.TIME_SERIES_TREND_LINE_WIDTH,
                                           line_dash=self.options.TIME_SERIES_TREND_LINE_DASH)
        self.plot_avg = self.figure.line('x', 'avg', color=self.options.PLOT_COLOR, source=self.source['bound'],
                                         line_width=self.options.TIME_SERIES_AVG_LINE_WIDTH,
                                         line_dash=self.options.TIME_SERIES_AVG_LINE_DASH)
        self.plot_patch = self.figure.patch('x', 'y', color=self.options.PLOT_COLOR, source=self.source['patch'],
                                            alpha=self.options.TIME_SERIES_PATCH_ALPHA)

    def __add_histogram_data(self):
        self.histogram = figure(tools="")
        self.histogram.xaxis.axis_label_text_font_size = self.options.PLOT_AXIS_LABEL_FONT_SIZE
        self.histogram.yaxis.axis_label_text_font_size = self.options.PLOT_AXIS_LABEL_FONT_SIZE
        self.histogram.xaxis.major_label_text_font_size = self.options.PLOT_AXIS_MAJOR_LABEL_FONT_SIZE
        self.histogram.yaxis.major_label_text_font_size = self.options.PLOT_AXIS_MAJOR_LABEL_FONT_SIZE
        self.histogram.min_border_left = self.options.MIN_BORDER
        self.histogram.min_border_bottom = self.options.MIN_BORDER
        self.vbar = self.histogram.vbar(x='x', width='width', bottom=0, top='top', source=self.source['hist'],
                                        color=self.options.PLOT_COLOR, alpha=self.options.HISTOGRAM_ALPHA)

        self.histogram.xaxis.axis_label = ""
        self.histogram.yaxis.axis_label = "Frequency"

    def __add_legend(self):
        # Set the legend
        legend_plot = Legend(items=[("Data  ", [self.plot_data]),
                                    ("Series Average  ", [self.plot_avg]),
                                    ("Rolling Average  ", [self.plot_trend]),
                                    ("Percentile Region  ", [self.plot_patch])],
                             orientation='horizontal')

        # Add the layout outside the plot, clicking legend item hides the line
        self.figure.add_layout(legend_plot, 'above')
        self.figure.legend.click_policy = "hide"

    def __add_hover(self):
        self.figure.add_tools(HoverTool(show_arrow=True,
                                        tooltips=[('ID', '@mrn'),
                                                  ('Date', '@x{%F}'),
                                                  ('Value', '@y{0.2f}')],
                                        formatters={'x': 'datetime'}))

        self.histogram.add_tools(HoverTool(show_arrow=True, line_policy='next', mode='vline',
                                           tooltips=[('Bin Center', '@x{0.2f}'),
                                                     ('Counts', '@top')]))

    def __do_layout(self):
        self.bokeh_layout = column(self.figure,
                                   self.div,
                                   self.histogram)

    def update_plot(self, x, y, mrn, uid, y_axis_label='Y Axis', avg_len=1, percentile=90., bin_size=10):

        self.set_figure_dimensions()

        self.y_axis_label = y_axis_label
        self.clear_sources()
        self.figure.yaxis.axis_label = y_axis_label
        self.figure.xaxis.axis_label = 'Simulation Date'
        self.histogram.xaxis.axis_label = y_axis_label

        self.update_plot_data(x, y, mrn, uid)
        self.update_histogram(bin_size=bin_size)
        self.update_trend(avg_len, percentile)

        self.update_bokeh_layout_in_wx_python()

    def update_plot_data(self, x, y, mrn, uid):
        valid_indices = [i for i, value in enumerate(y) if value != 'None']
        self.source['plot'].data = {'x': [value for i, value in enumerate(x) if i in valid_indices],
                                    'y': [value for i, value in enumerate(y) if i in valid_indices],
                                    'mrn': [value for i, value in enumerate(mrn) if i in valid_indices],
                                    'uid': [value for i, value in enumerate(uid) if i in valid_indices]}

    def update_histogram(self, bin_size=10):
        width_fraction = 0.9
        hist, bins = np.histogram(self.source['plot'].data['y'], bins=bin_size)
        width = [width_fraction * (bins[1] - bins[0])] * bin_size
        center = (bins[:-1] + bins[1:]) / 2.
        self.source['hist'].data = {'x': center, 'top': hist, 'width': width}

    def update_trend(self, avg_len, percentile):

        x = self.source['plot'].data['x']
        y = self.source['plot'].data['y']
        if x and y:
            x_len = len(x)

            data_collapsed = collapse_into_single_dates(x, y)
            x_trend, y_trend = moving_avg(data_collapsed, avg_len)

            y_np = np.array(self.source['plot'].data['y'])
            upper_bound = float(np.percentile(y_np, 50. + percentile / 2.))
            average = float(np.percentile(y_np, 50))
            lower_bound = float(np.percentile(y_np, 50. - percentile / 2.))

            self.source['trend'].data = {'x': x_trend,
                                         'y': y_trend,
                                         'mrn': ['Avg'] * len(x_trend)}
            self.source['bound'].data = {'x': [x[0], x[-1]],
                                         'mrn': ['Series Avg'] * 2,
                                         'upper': [upper_bound] * 2,
                                         'avg': [average] * 2,
                                         'lower': [lower_bound] * 2,
                                         'y': [average] * 2}
            self.source['patch'].data = {'x': [x[0], x[-1], x[-1], x[0]],
                                         'y': [upper_bound, upper_bound, lower_bound, lower_bound]}

    def get_csv(self):
        data = self.source['plot'].data
        csv_data = ['MRN,Study Instance UID,Date,%s' % self.y_axis_label]
        for i in range(len(data['mrn'])):
            csv_data.append(','.join(str(data[key][i]).replace(',', '^') for key in ['mrn', 'uid', 'x', 'y']))
        return '\n'.join(csv_data)

    def set_figure_dimensions(self):
        panel_width, panel_height = self.parent.GetSize()
        self.figure.plot_width = int(self.size_factor['plot'][0] * float(panel_width))
        self.figure.plot_height = int(self.size_factor['plot'][1] * float(panel_height))
        self.histogram.plot_width = int(self.size_factor['hist'][0] * float(panel_width))
        self.histogram.plot_height = int(self.size_factor['hist'][1] * float(panel_height))
        self.div.width = int(self.size_factor['plot'][0] * float(panel_width))


class PlotRegression(Plot):
    """
    Generate plot for Regression tab
    """
    def __init__(self, parent, options):
        """
        :param parent: the wx UI object where the plot will be displayed
        :param options: user preferences
        :type options: Options
        """
        Plot.__init__(self, parent, options)

        self.type = 'regression'
        self.parent = parent
        self.size_factor = {'plot': (0.927, 0.421),
                            'table': (0.927, 0.140),
                            'resid': (0.464, 0.281),
                            'prob': (0.464, 0.281)}

        self.x_axis_title, self.y_axis_title = '', ''
        self.reg = None
        self.options = options
        self.source = {'plot': ColumnDataSource(data=dict(x=[], y=[], mrn=[], uid=[], dates=[])),
                       'trend': ColumnDataSource(data=dict(x=[], y=[], mrn=[])),
                       'residuals': ColumnDataSource(data=dict(x=[], y=[], mrn=[], date=[])),
                       'residuals_zero': ColumnDataSource(data=dict(x=[], y=[], mrn=[])),
                       'prob': ColumnDataSource(data=dict(x=[], y=[], mrn=[])),
                       'prob_45': ColumnDataSource(data=dict(x=[], y=[])),
                       'table': ColumnDataSource(data=dict(var=[], coef=[], std_err=[], t_value=[], p_value=[],
                                                           spacer=[], fit_param=[]))}

        self.__create_additional_figures()
        self.__create_table()
        self.__add_plot_data()
        self.__add_hover()
        self.__do_layout()

    def __create_additional_figures(self):
        self.figure_residual_fits = figure(tools="pan,box_zoom,crosshair,reset")
        self.figure_residual_fits.xaxis.axis_label = 'Fitted Values'
        self.figure_residual_fits.yaxis.axis_label = 'Residuals'
        self.figure_prob_plot = figure(tools="pan,box_zoom,crosshair,reset")
        self.figure_prob_plot.xaxis.axis_label = 'Quantiles'
        self.figure_prob_plot.yaxis.axis_label = 'Ordered Values'

    def __create_table(self):
        columns = [TableColumn(field="var", title="", width=100),
                   TableColumn(field="coef", title="Coef", formatter=NumberFormatter(format="0.000"), width=50),
                   TableColumn(field="std_err", title="Std. Err.", formatter=NumberFormatter(format="0.000"), width=50),
                   TableColumn(field="t_value", title="t-value", formatter=NumberFormatter(format="0.000"), width=50),
                   TableColumn(field="p_value", title="p-value", formatter=NumberFormatter(format="0.000"), width=50),
                   TableColumn(field="spacer", title="", width=2),
                   TableColumn(field="fit_param", title="", width=75)]
        self.regression_table = DataTable(source=self.source['table'], columns=columns, index_position=None)

    def __add_plot_data(self):
        self.plot_data = self.figure.circle('x', 'y', source=self.source['plot'], size=self.options.REGRESSION_CIRCLE_SIZE,
                                            alpha=self.options.REGRESSION_ALPHA, color=self.options.PLOT_COLOR)
        self.plot_trend = self.figure.line('x', 'y', color=self.options.PLOT_COLOR, source=self.source['trend'],
                                           line_width=self.options.REGRESSION_LINE_WIDTH,
                                           line_dash=self.options.REGRESSION_LINE_DASH)
        self.plot_residuals = self.figure_residual_fits.circle('x', 'y', source=self.source['residuals'],
                                                               size=self.options.REGRESSION_RESIDUAL_CIRCLE_SIZE,
                                                               alpha=self.options.REGRESSION_RESIDUAL_ALPHA,
                                                               color=self.options.PLOT_COLOR)
        self.plot_residuals_zero = self.figure_residual_fits.line('x', 'y', source=self.source['residuals_zero'],
                                                                  line_width=self.options.REGRESSION_RESIDUAL_LINE_WIDTH,
                                                                  line_dash=self.options.REGRESSION_RESIDUAL_LINE_DASH,
                                                                  alpha=self.options.REGRESSION_RESIDUAL_ALPHA,
                                                                  color=self.options.REGRESSION_RESIDUAL_LINE_COLOR)
        self.plot_prob = self.figure_prob_plot.circle('x', 'y', source=self.source['prob'],
                                                      size=self.options.REGRESSION_RESIDUAL_CIRCLE_SIZE,
                                                      alpha=self.options.REGRESSION_RESIDUAL_ALPHA,
                                                      color=self.options.PLOT_COLOR)
        self.plot_prob_45 = self.figure_prob_plot.line('x', 'y', source=self.source['prob_45'],
                                                       line_width=self.options.REGRESSION_RESIDUAL_LINE_WIDTH,
                                                       line_dash=self.options.REGRESSION_RESIDUAL_LINE_DASH,
                                                       alpha=self.options.REGRESSION_RESIDUAL_ALPHA,
                                                       color=self.options.REGRESSION_RESIDUAL_LINE_COLOR)

    def __add_hover(self):
        self.figure.add_tools(HoverTool(show_arrow=True,
                                        tooltips=[('ID', '@mrn'),
                                                  ('x', '@x{0.2f}'),
                                                  ('y', '@y{0.2f}')]))

        self.figure_residual_fits.add_tools(HoverTool(show_arrow=True,
                                            tooltips=[('ID', '@mrn'),
                                                      ('Date', '@date{%F}'),
                                                      ('x', '@x{0.2f}'),
                                                      ('y', '@y{0.2f}')],
                                            formatters={'date': 'datetime'}))

        self.figure_prob_plot.add_tools(HoverTool(show_arrow=True,
                                                  tooltips=[('x', '@x{0.2f}'),
                                                            ('y', '@y{0.2f}')]))

    def __do_layout(self):
        self.bokeh_layout = column(self.figure,
                                   self.regression_table,
                                   row(self.figure_residual_fits, self.figure_prob_plot))

    def update_plot(self, plot_data, x_var, x_axis_title, y_axis_title):
        self.set_figure_dimensions()
        self.x_axis_title, self.y_axis_title = x_axis_title, y_axis_title
        self.clear_sources()
        self.source['plot'].data = plot_data
        self.update_trend(x_var)
        self.figure.xaxis.axis_label = x_axis_title
        self.figure.yaxis.axis_label = y_axis_title
        self.update_bokeh_layout_in_wx_python()

    def set_figure_dimensions(self):
        panel_width, panel_height = self.parent.GetSize()
        self.figure.plot_width = int(self.size_factor['plot'][0] * float(panel_width))
        self.figure.plot_height = int(self.size_factor['plot'][1] * float(panel_height))
        self.figure_residual_fits.plot_width = int(self.size_factor['resid'][0] * float(panel_width))
        self.figure_residual_fits.plot_height = int(self.size_factor['resid'][1] * float(panel_height))
        self.figure_prob_plot.plot_width = int(self.size_factor['prob'][0] * float(panel_width))
        self.figure_prob_plot.plot_height = int(self.size_factor['prob'][1] * float(panel_height))
        self.regression_table.width = int(self.size_factor['table'][0] * float(panel_width))
        self.regression_table.height = int(self.size_factor['table'][1] * float(panel_height))

    def update_trend(self, x_var):
        x, y, mrn, date = self.clean_data(self.source['plot'].data['x'],
                                          self.source['plot'].data['y'],
                                          mrn=self.source['plot'].data['mrn'],
                                          dates=self.source['plot'].data['date'])

        data = np.array([y, x])
        clean_data = data[:, ~np.any(np.isnan(data), axis=0)]
        X = np.transpose(clean_data[1:])
        y = clean_data[0]

        self.reg = MultiVariableRegression(X, y)

        x_trend = [min(x), max(x)]
        y_trend = np.add(np.multiply(x_trend, self.reg.slope), self.reg.y_intercept)

        self.source['residuals'].data = {'x': self.reg.predictions,
                                         'y': self.reg.residuals,
                                         'mrn': mrn,
                                         'date': date}

        self.source['residuals_zero'].data = {'x': [min(self.reg.predictions), max(self.reg.predictions)],
                                              'y': [0, 0],
                                              'mrn': [None, None]}

        self.source['prob'].data = {'x': self.reg.norm_prob_plot[0],
                                    'y': self.reg.norm_prob_plot[1]}

        self.source['prob_45'].data = {'x': self.reg.x_trend_prob,
                                       'y': self.reg.y_trend_prob}

        self.source['table'].data = {'var': ['y-int', x_var],
                                     'coef': [self.reg.y_intercept, self.reg.slope],
                                     'std_err': self.reg.sd_b,
                                     't_value': self.reg.ts_b,
                                     'p_value': self.reg.p_values,
                                     'spacer': ['', ''],
                                     'fit_param': ["R²: %0.3f" % self.reg.r_sq, "MSE: %0.3f" % self.reg.mse]}

        self.source['trend'].data = {'x': x_trend,
                                     'y': y_trend,
                                     'mrn': ['Trend'] * 2}

    def get_csv_data(self):
        plot_data = self.source['plot'].data
        csv_data = ['Linear Regression',
                    'Data',
                    ',MRN,%s' % ','.join(plot_data['mrn']),
                    ',Study Instance UID,%s' % ','.join(plot_data['uid']),
                    ',Sim Study Date,%s' % ','.join(plot_data['date']),
                    'Independent,%s,%s' % (self.y_axis_title, ','.join(str(a) for a in plot_data['y'])),
                    'Dependent,%s,%s' % (self.x_axis_title, ','.join(str(a) for a in plot_data['x'])),
                    '',
                    self.get_csv_model(),
                    '',
                    self.get_csv_analysis()]

        return '\n'.join(csv_data)

    def get_csv_model(self):
        data = self.source['table'].data
        csv_model = ['Model',
                     ',Coef,Std. Err.,t-value,p-value']
        for i in range(len(data['var'])):
            csv_model.append(self.get_csv_model_row(i))

        csv_model.extend(["R^2,%s" % self.reg.r_sq,
                          "MSE,%s" % self.reg.mse])

        return '\n'.join(csv_model)

    def get_csv_analysis(self):
        return '\n'.join(['Analysis',
                          'Quantiles,%s' % ','.join(str(v) for v in self.reg.norm_prob_plot[0]),
                          'Ordered Values,%s' % ','.join(str(v) for v in self.reg.norm_prob_plot[1]),
                          '',
                          'Residuals,%s' % ','.join(str(v) for v in self.reg.residuals),
                          'Fitted Values,%s' % ','.join(str(v) for v in self.reg.predictions)])

    def get_csv_model_row(self, index):
        data = self.source['table'].data
        variables = ['var', 'coef', 'std_err', 't_value', 'p_value']
        return ','.join([str(data[var][index]) for var in variables])


class PlotMultiVarRegression(Plot):
    """
    Class to generate plot for MultiVariable Frame created from Regression tab
    """
    def __init__(self, parent, options):
        """
        :param parent: the wx UI object where the plot will be displayed
        :param options: user preferences
        :type options: Options
        """
        Plot.__init__(self, parent, options)

        self.type = 'multi-variable_regression'
        self.parent = parent

        self.size_factor = {'resid': (0.475, 0.45),
                            'prob': (0.475, 0.45),
                            'table': (0.95, 0.45)}

        self.options = options
        self.X, self.y = None, None
        self.x_variables, self.y_variable, self.stats_data = None, None, None
        self.mrn, self.uid, self.dates = None, None, None
        self.reg = None
        self.source = {'plot': ColumnDataSource(data=dict(x=[], y=[], mrn=[], uid=[], date=[])),
                       'trend': ColumnDataSource(data=dict(x=[], y=[], mrn=[])),
                       'residuals': ColumnDataSource(data=dict(x=[], y=[], mrn=[], date=[])),
                       'residuals_zero': ColumnDataSource(data=dict(x=[], y=[], mrn=[])),
                       'prob': ColumnDataSource(data=dict(x=[], y=[], mrn=[])),
                       'prob_45': ColumnDataSource(data=dict(x=[], y=[])),
                       'table': ColumnDataSource(data=dict(var=[], coef=[], std_err=[], t_value=[], p_value=[],
                                                           spacer=[], fit_param=[]))}

        self.__add_additional_figures()
        self.__add_plot_data()
        self.__add_hover()
        self.__create_table()
        self.__do_layout()

    def __add_additional_figures(self):
        self.figure_prob_plot = figure(tools=DEFAULT_TOOLS)
        self.figure_prob_plot.xaxis.axis_label = 'Quantiles'
        self.figure_prob_plot.yaxis.axis_label = 'Ordered Values'

        self.figure_prob_plot.xaxis.axis_label_text_font_size = self.options.PLOT_AXIS_LABEL_FONT_SIZE
        self.figure_prob_plot.yaxis.axis_label_text_font_size = self.options.PLOT_AXIS_LABEL_FONT_SIZE
        self.figure_prob_plot.xaxis.major_label_text_font_size = self.options.PLOT_AXIS_MAJOR_LABEL_FONT_SIZE
        self.figure_prob_plot.yaxis.major_label_text_font_size = self.options.PLOT_AXIS_MAJOR_LABEL_FONT_SIZE

    def __add_plot_data(self):
        self.plot_residuals = self.figure.circle('x', 'y', source=self.source['residuals'],
                                                 size=self.options.REGRESSION_RESIDUAL_CIRCLE_SIZE,
                                                 alpha=self.options.REGRESSION_RESIDUAL_ALPHA,
                                                 color=self.options.PLOT_COLOR)
        self.plot_residuals_zero = self.figure.line('x', 'y', source=self.source['residuals_zero'],
                                                    line_width=self.options.REGRESSION_RESIDUAL_LINE_WIDTH,
                                                    line_dash=self.options.REGRESSION_RESIDUAL_LINE_DASH,
                                                    alpha=self.options.REGRESSION_RESIDUAL_ALPHA,
                                                    color=self.options.REGRESSION_RESIDUAL_LINE_COLOR)
        self.plot_prob = self.figure_prob_plot.circle('x', 'y', source=self.source['prob'],
                                                      size=self.options.REGRESSION_RESIDUAL_CIRCLE_SIZE,
                                                      alpha=self.options.REGRESSION_RESIDUAL_ALPHA,
                                                      color=self.options.PLOT_COLOR)
        self.plot_prob_45 = self.figure_prob_plot.line('x', 'y', source=self.source['prob_45'],
                                                       line_width=self.options.REGRESSION_RESIDUAL_LINE_WIDTH,
                                                       line_dash=self.options.REGRESSION_RESIDUAL_LINE_DASH,
                                                       alpha=self.options.REGRESSION_RESIDUAL_ALPHA,
                                                       color=self.options.REGRESSION_RESIDUAL_LINE_COLOR)

    def __add_hover(self):
        self.figure.add_tools(HoverTool(show_arrow=True,
                                        tooltips=[('ID', '@mrn'),
                                                  ('Date', '@date{%F}'),
                                                  ('x', '@x{0.2f}'),
                                                  ('y', '@y{0.2f}')],
                                        formatters={'date': 'datetime'}))

        self.figure_prob_plot.add_tools(HoverTool(show_arrow=True,
                                                  tooltips=[('x', '@x{0.2f}'),
                                                            ('y', '@y{0.2f}')]))

    def __create_table(self):
        columns = [TableColumn(field="var", title="", width=100),
                   TableColumn(field="coef", title="Coef", formatter=NumberFormatter(format="0.000"), width=40),
                   TableColumn(field="std_err", title="Std. Err.", formatter=NumberFormatter(format="0.000"), width=40),
                   TableColumn(field="t_value", title="t-value", formatter=NumberFormatter(format="0.000"), width=40),
                   TableColumn(field="p_value", title="p-value", formatter=NumberFormatter(format="0.000"), width=40),
                   TableColumn(field="spacer", title="", width=5),
                   TableColumn(field="fit_param", title="", width=75)]
        self.regression_table = DataTable(source=self.source['table'], columns=columns, index_position=None)

    def __do_layout(self):
        self.bokeh_layout = column(row(self.figure, self.figure_prob_plot),
                                   self.regression_table)

    def set_figure_dimensions(self):
        panel_width, panel_height = self.parent.GetSize()
        self.figure.plot_width = int(self.size_factor['resid'][0] * float(panel_width))
        self.figure.plot_height = int(self.size_factor['resid'][1] * float(panel_height))
        self.figure_prob_plot.plot_width = int(self.size_factor['prob'][0] * float(panel_width))
        self.figure_prob_plot.plot_height = int(self.size_factor['prob'][1] * float(panel_height))
        self.regression_table.width = int(self.size_factor['table'][0] * float(panel_width))
        self.regression_table.height = int(self.size_factor['table'][1] * float(panel_height))

    def update_plot(self, y_variable, x_variables, stats_data):
        self.set_figure_dimensions()
        self.y_variable, self.x_variables = y_variable, x_variables
        self.stats_data = stats_data
        self.clear_sources()
        x_len = len(x_variables)
        self.X, self.y, self.mrn, self.uid, self.dates = stats_data.get_X_and_y(y_variable, x_variables,
                                                                                include_patient_info=True)

        self.reg = MultiVariableRegression(self.X, self.y)

        self.source['residuals'].data = {'x': self.reg.predictions,
                                         'y': self.reg.residuals,
                                         'mrn': self.mrn,
                                         'date': self.dates}

        self.source['residuals_zero'].data = {'x': [min(self.reg.predictions), max(self.reg.predictions)],
                                              'y': [0, 0],
                                              'mrn': [None, None]}

        self.source['prob'].data = {'x': self.reg.norm_prob_plot[0],
                                    'y': self.reg.norm_prob_plot[1]}

        self.source['prob_45'].data = {'x': self.reg.x_trend_prob,
                                       'y': self.reg.y_trend_prob}

        fit_param = [''] * (x_len + 1)
        fit_param[0] = "R²: %0.3f ----- MSE: %0.3f" % (self.reg.r_sq, self.reg.mse)
        fit_param[1] = "f stat: %0.3f ---- p value: %0.3f" % (self.reg.f_stat, self.reg.f_p_value)
        self.source['table'].data = {'var': ['y-int'] + x_variables,
                                     'coef': [self.reg.y_intercept] + self.reg.slope.tolist(),
                                     'std_err': self.reg.sd_b,
                                     't_value': self.reg.ts_b,
                                     'p_value': self.reg.p_values,
                                     'spacer': [''] * (x_len + 1),
                                     'fit_param': fit_param}

        self.figure.xaxis.axis_label = 'Fitted Values'
        self.figure.yaxis.axis_label = 'Residuals'

        self.update_bokeh_layout_in_wx_python()

    def get_csv_data(self):
        csv_data = ['Multi-Variable Regression',
                    'Data',
                    ',MRN,%s' % ','.join(self.mrn),
                    ',Study Instance UID,%s' % ','.join(self.uid),
                    ',Sim Study Date,%s' % ','.join(self.dates),
                    self.get_regression_csv_row(self.y_variable, self.y, var_type='Dependent')]

        for i, x_variable in enumerate(self.x_variables):
            csv_data.append(self.get_regression_csv_row(x_variable, self.X[:, i]))

        csv_data.append('')
        csv_data.append(self.get_csv_model())

        csv_data.append('')
        csv_data.append(self.get_csv_analysis())

        return '\n'.join(csv_data)

    def get_csv_model(self):
        data = self.source['table'].data
        csv_model = ['Model',
                     ',Coef,Std. Err.,t-value,p-value']
        for i in range(len(data['var'])):
            csv_model.append(self.get_csv_model_row(i))

        csv_model.extend(["R^2,%s" % self.reg.r_sq,
                          "MSE,%s" % self.reg.mse,
                          "f-stat,%s" % self.reg.f_stat,
                          "f p-value,%s" % self.reg.f_p_value])

        return '\n'.join(csv_model)

    def get_csv_analysis(self):
        return '\n'.join(['Analysis',
                          'Quantiles,%s' % ','.join(str(v) for v in self.reg.norm_prob_plot[0]),
                          'Ordered Values,%s' % ','.join(str(v) for v in self.reg.norm_prob_plot[1]),
                          '',
                          'Residuals,%s' % ','.join(str(v) for v in self.reg.residuals),
                          'Fitted Values,%s' % ','.join(str(v) for v in self.reg.predictions)])

    def get_csv_model_row(self, index):
        data = self.source['table'].data
        variables = ['var', 'coef', 'std_err', 't_value', 'p_value']
        return ','.join([str(data[var][index]) for var in variables])

    @staticmethod
    def get_regression_csv_row(var_name, data, var_type='Independent'):
        return '%s,%s,%s' % (var_type, var_name, ','.join(str(a) for a in data))

    @property
    def final_stats_data(self):
        return {'X': self.X, 'y': self.y,
                'x_variables': self.x_variables,
                'y_variable': self.y_variable,
                'multi_var_pred': self.reg.predictions,
                'multi_var_mse': self.reg.mse,
                'options': self.options,
                'mrn': self.mrn,
                'study_date': self.dates,
                'uid': self.uid}


class PlotControlChart(Plot):
    """
    Generate plot for Control Chart frame
    """
    def __init__(self, parent, options):
        """
        :param parent: the wx UI object where the plot will be displayed
        :param options: user preferences
        :type options: Options
        """
        Plot.__init__(self, parent, options, x_axis_label='Study')

        self.type = 'control_chart'
        self.parent = parent
        self.size_factor = {'plot': (0.940, 0.359)}

        self.y_axis_label = ''
        self.options = options
        self.source = {'plot': ColumnDataSource(data=dict(x=[], y=[], mrn=[], color=[], alpha=[], dates=[])),
                       'center_line': ColumnDataSource(data=dict(x=[], y=[], mrn=[])),
                       'ucl_line': ColumnDataSource(data=dict(x=[], y=[], mrn=[])),
                       'lcl_line': ColumnDataSource(data=dict(x=[], y=[], mrn=[])),
                       'bound': ColumnDataSource(data=dict(x=[], mrn=[], upper=[], avg=[], lower=[])),
                       'patch': ColumnDataSource(data=dict(x=[], y=[])),
                       'adj_plot': ColumnDataSource(data=dict(x=[], y=[], mrn=[], color=[], alpha=[], dates=[])),
                       'adj_center_line': ColumnDataSource(data=dict(x=[], y=[], mrn=[])),
                       'adj_ucl_line': ColumnDataSource(data=dict(x=[], y=[], mrn=[])),
                       'adj_lcl_line': ColumnDataSource(data=dict(x=[], y=[], mrn=[])),
                       'adj_bound': ColumnDataSource(data=dict(x=[], mrn=[], upper=[], avg=[], lower=[])),
                       'adj_patch': ColumnDataSource(data=dict(x=[], y=[]))}

        self.__add_adj_figure()
        self.__add_plot_data()
        self.__add_hover()
        self.__create_divs()
        self.__add_legend()
        self.__do_layout()

    def __add_adj_figure(self):
        self.adj_figure = figure()
        self.adj_figure.xaxis.axis_label = 'Study'
        self.adj_figure.yaxis.axis_label = 'Residual'
        self.adj_figure.xaxis.axis_label_text_font_size = self.options.PLOT_AXIS_LABEL_FONT_SIZE
        self.adj_figure.yaxis.axis_label_text_font_size = self.options.PLOT_AXIS_LABEL_FONT_SIZE
        self.adj_figure.xaxis.major_label_text_font_size = self.options.PLOT_AXIS_MAJOR_LABEL_FONT_SIZE
        self.adj_figure.yaxis.major_label_text_font_size = self.options.PLOT_AXIS_MAJOR_LABEL_FONT_SIZE
        self.adj_figure.min_border = self.options.MIN_BORDER
        self.adj_figure.yaxis.axis_label_text_baseline = "bottom"

    def __add_plot_data(self):
        self.plot_data = self.figure.circle('x', 'y', source=self.source['plot'],
                                            size=self.options.CONTROL_CHART_CIRCLE_SIZE,
                                            alpha='alpha',
                                            color='color')
        self.plot_data_line = self.figure.line('x', 'y', source=self.source['plot'],
                                               line_width=self.options.CONTROL_CHART_LINE_WIDTH,
                                               color=self.options.CONTROL_CHART_LINE_COLOR,
                                               line_dash=self.options.CONTROL_CHART_LINE_DASH)
        self.plot_patch = self.figure.patch('x', 'y', color=self.options.PLOT_COLOR, source=self.source['patch'],
                                            alpha=self.options.CONTROL_CHART_PATCH_ALPHA)
        self.plot_center_line = self.figure.line('x', 'y', source=self.source['center_line'],
                                                 line_width=self.options.CONTROL_CHART_CENTER_LINE_WIDTH,
                                                 alpha=self.options.CONTROL_CHART_CENTER_LINE_ALPHA,
                                                 color=self.options.CONTROL_CHART_CENTER_LINE_COLOR,
                                                 line_dash=self.options.CONTROL_CHART_CENTER_LINE_DASH)
        self.plot_lcl_line = self.figure.line('x', 'y', source=self.source['lcl_line'],
                                              line_width=self.options.CONTROL_CHART_LCL_LINE_WIDTH,
                                              alpha=self.options.CONTROL_CHART_LCL_LINE_ALPHA,
                                              color=self.options.CONTROL_CHART_LCL_LINE_COLOR,
                                              line_dash=self.options.CONTROL_CHART_LCL_LINE_DASH)
        self.plot_ucl_line = self.figure.line('x', 'y', source=self.source['ucl_line'],
                                              line_width=self.options.CONTROL_CHART_UCL_LINE_WIDTH,
                                              alpha=self.options.CONTROL_CHART_UCL_LINE_ALPHA,
                                              color=self.options.CONTROL_CHART_UCL_LINE_COLOR,
                                              line_dash=self.options.CONTROL_CHART_UCL_LINE_DASH)

        self.adj_plot_data = self.adj_figure.circle('x', 'y', source=self.source['adj_plot'],
                                                    size=self.options.CONTROL_CHART_CIRCLE_SIZE,
                                                    alpha='alpha',
                                                    color='color')
        self.adj_plot_data_line = self.adj_figure.line('x', 'y', source=self.source['adj_plot'],
                                                       line_width=self.options.CONTROL_CHART_LINE_WIDTH,
                                                       color=self.options.CONTROL_CHART_LINE_COLOR,
                                                       line_dash=self.options.CONTROL_CHART_LINE_DASH)
        self.adj_plot_patch = self.adj_figure.patch('x', 'y', color=self.options.PLOT_COLOR, source=self.source['adj_patch'],
                                                    alpha=self.options.CONTROL_CHART_PATCH_ALPHA)
        self.adj_plot_center_line = self.adj_figure.line('x', 'y', source=self.source['adj_center_line'],
                                                         line_width=self.options.CONTROL_CHART_CENTER_LINE_WIDTH,
                                                         alpha=self.options.CONTROL_CHART_CENTER_LINE_ALPHA,
                                                         color=self.options.CONTROL_CHART_CENTER_LINE_COLOR,
                                                         line_dash=self.options.CONTROL_CHART_CENTER_LINE_DASH)
        self.adj_plot_lcl_line = self.adj_figure.line('x', 'y', source=self.source['adj_lcl_line'],
                                                      line_width=self.options.CONTROL_CHART_LCL_LINE_WIDTH,
                                                      alpha=self.options.CONTROL_CHART_LCL_LINE_ALPHA,
                                                      color=self.options.CONTROL_CHART_LCL_LINE_COLOR,
                                                      line_dash=self.options.CONTROL_CHART_LCL_LINE_DASH)
        self.adj_plot_ucl_line = self.adj_figure.line('x', 'y', source=self.source['adj_ucl_line'],
                                                      line_width=self.options.CONTROL_CHART_UCL_LINE_WIDTH,
                                                      alpha=self.options.CONTROL_CHART_UCL_LINE_ALPHA,
                                                      color=self.options.CONTROL_CHART_UCL_LINE_COLOR,
                                                      line_dash=self.options.CONTROL_CHART_UCL_LINE_DASH)

    def __add_hover(self):
        self.figure.add_tools(HoverTool(show_arrow=True,
                                        tooltips=[('ID', '@mrn'),
                                                  ('Date', '@dates{%F}'),
                                                  ('Study', '@x'),
                                                  ('Value', '@y{0.2f}')],
                                        formatters={'dates': 'datetime'}))

        self.adj_figure.add_tools(HoverTool(show_arrow=True,
                                            tooltips=[('ID', '@mrn'),
                                                      ('Date', '@dates{%F}'),
                                                      ('Study', '@x'),
                                                      ('Value', '@y{0.2f}')],
                                            formatters={'dates': 'datetime'}))

    def __add_legend(self):
        # Set the legend
        legend_plot = Legend(items=[("Charting Variable   ", [self.plot_data]),
                                    ("Charting Variable Line  ", [self.plot_data_line]),
                                    ('Center Line   ', [self.plot_center_line]),
                                    ('UCL  ', [self.plot_ucl_line]),
                                    ('LCL  ', [self.plot_lcl_line])],
                             orientation='horizontal')

        adj_legend_plot = Legend(items=[("Residuals   ", [self.adj_plot_data]),
                                        ("Residuals Line  ", [self.adj_plot_data_line]),
                                        ('Center Line   ', [self.adj_plot_center_line]),
                                        ('UCL  ', [self.adj_plot_ucl_line]),
                                        ('LCL  ', [self.adj_plot_lcl_line])],
                                 orientation='horizontal')

        # Add the layout outside the plot, clicking legend item hides the line
        self.figure.add_layout(legend_plot, 'above')
        self.figure.legend.click_policy = "hide"
        self.adj_figure.add_layout(adj_legend_plot, 'above')
        self.adj_figure.legend.click_policy = "hide"

    def __create_divs(self):
        self.div_center_line = Div(text='', width=175)
        self.div_ucl = Div(text='', width=175)
        self.div_lcl = Div(text='', width=175)

        self.div_adj_center_line = Div(text='', width=175)
        self.div_adj_ucl = Div(text='', width=175)
        self.div_adj_lcl = Div(text='', width=175)

    def __do_layout(self):
        self.bokeh_layout = column(self.figure,
                                   row(self.div_center_line, self.div_ucl, self.div_lcl),
                                   self.adj_figure,
                                   row(self.div_adj_center_line, self.div_adj_ucl, self.div_adj_lcl))

    def set_figure_dimensions(self):
        panel_width, panel_height = self.parent.GetSize()
        self.figure.plot_width = int(self.size_factor['plot'][0] * float(panel_width))
        self.figure.plot_height = int(self.size_factor['plot'][1] * float(panel_height))
        self.adj_figure.plot_width = int(self.size_factor['plot'][0] * float(panel_width))
        self.adj_figure.plot_height = int(self.size_factor['plot'][1] * float(panel_height))

    def update_plot(self, x, y, mrn, uid, dates, y_axis_label='Y Axis', update_layout=True):
        self.set_figure_dimensions()
        self.clear_sources()
        self.y_axis_label = y_axis_label
        self.figure.yaxis.axis_label = self.y_axis_label

        x, y, mrn, uid, dates = self.clean_data(x, y, mrn=mrn, uid=uid, dates=dates)

        center_line, ucl, lcl = get_control_limits(y)

        colors = [self.options.CONTROL_CHART_OUT_OF_CONTROL_COLOR, self.options.PLOT_COLOR]
        alphas = [self.options.CONTROL_CHART_OUT_OF_CONTROL_ALPHA, self.options.CONTROL_CHART_CIRCLE_ALPHA]
        color = [colors[ucl > value > lcl] for value in y]
        alpha = [alphas[ucl > value > lcl] for value in y]

        self.source['plot'].data = {'x': x, 'y': y, 'mrn': mrn, 'uid': uid,
                                    'color': color, 'alpha': alpha, 'dates': dates}

        self.source['patch'].data = {'x': [x[0], x[-1], x[-1], x[0]],
                                     'y': [ucl, ucl, lcl, lcl]}
        self.source['center_line'].data = {'x': [min(x), max(x)],
                                           'y': [center_line] * 2,
                                           'mrn': ['center line'] * 2}

        self.source['lcl_line'].data = {'x': [min(x), max(x)],
                                        'y': [lcl] * 2,
                                        'mrn': ['center line'] * 2}
        self.source['ucl_line'].data = {'x': [min(x), max(x)],
                                        'y': [ucl] * 2,
                                        'mrn': ['center line'] * 2}

        self.div_center_line.text = "<b>Center line</b>: %0.3f" % center_line
        self.div_ucl.text = "<b>UCL</b>: %0.3f" % ucl
        self.div_lcl.text = "<b>LCL</b>: %0.3f" % lcl

        if update_layout:
            self.update_bokeh_layout_in_wx_python()

    def update_adjusted_control_chart(self, x, residuals, mrn, uid, dates, update_layout=True):

        center_line, ucl, lcl = get_control_limits(residuals)

        colors = [self.options.CONTROL_CHART_OUT_OF_CONTROL_COLOR, self.options.PLOT_COLOR]
        alphas = [self.options.CONTROL_CHART_OUT_OF_CONTROL_ALPHA, self.options.CONTROL_CHART_CIRCLE_ALPHA]
        color = [colors[ucl > value > lcl] for value in residuals]
        alpha = [alphas[ucl > value > lcl] for value in residuals]

        self.source['adj_plot'].data = {'x': x, 'y': residuals, 'mrn': mrn, 'uid': uid,
                                        'color': color, 'alpha': alpha, 'dates': dates}

        self.source['adj_patch'].data = {'x': [x[0], x[-1], x[-1], x[0]],
                                         'y': [ucl, ucl, lcl, lcl]}
        self.source['adj_center_line'].data = {'x': [min(x), max(x)],
                                               'y': [center_line] * 2,
                                               'mrn': ['center line'] * 2}

        self.source['adj_lcl_line'].data = {'x': [min(x), max(x)],
                                            'y': [lcl] * 2,
                                            'mrn': ['center line'] * 2}
        self.source['adj_ucl_line'].data = {'x': [min(x), max(x)],
                                            'y': [ucl] * 2,
                                            'mrn': ['center line'] * 2}

        self.div_adj_center_line.text = "<b>Center line</b>: %0.3f" % center_line
        self.div_adj_ucl.text = "<b>UCL</b>: %0.3f" % ucl
        self.div_adj_lcl.text = "<b>LCL</b>: %0.3f" % lcl

        if update_layout:
            self.update_bokeh_layout_in_wx_python()

    @staticmethod
    def get_adjusted_control_chart(y_variable, x_variables, regression, stats_data):

        X, y, mrn, uid, dates = stats_data.get_X_and_y(y_variable, x_variables, include_patient_info=True)
        predictions = regression.reg.predict(X)
        residuals = np.subtract(y, predictions)

        x = [i + 1 for i in range(len(y))]

        sort_index = sorted(range(len(dates)), key=lambda k: dates[k])
        dates_sorted, residuals_sorted, mrn_sorted, uid_sorted = [], [], [], []

        for s in range(len(dates)):
            dates_sorted.append(dates[sort_index[s]])
            residuals_sorted.append(residuals[sort_index[s]])
            mrn_sorted.append(mrn[sort_index[s]])
            uid_sorted.append(uid[sort_index[s]])

        return {'x': x, 'residuals': residuals_sorted,
                'mrn': mrn_sorted, 'uid': uid_sorted, 'dates': dates_sorted}

    def clear_plot(self):
        self.clear_div()  # super class does not have these Div objects
        super().clear_plot()

    def clear_sources(self):
        super().clear_sources()
        self.clear_div()

    def clear_div(self):
        self.div_center_line.text = "<b>Center line</b>:"
        self.div_ucl.text = "<b>UCL</b>:"
        self.div_lcl.text = "<b>LCL</b>:"
        self.div_adj_center_line.text = "<b>Center line</b>:"
        self.div_adj_ucl.text = "<b>UCL</b>:"
        self.div_adj_lcl.text = "<b>LCL</b>:"

    def get_csv(self):

        data = self.source['plot'].data
        resid = self.source['adj_plot'].data['y']
        if resid:
            residual_column = ',Residual%s' % [' (%s)' % self.model_name, ''][self.model_name is None]
        else:
            residual_column = ''
        csv_data = ['MRN,Study Instance UID,Study #,Date,%s%s' % (self.y_axis_label, residual_column)]
        for i in range(len(data['mrn'])):
            csv_data.append(','.join(str(data[key][i]).replace(',', '^') for key in ['mrn', 'uid', 'x', 'dates', 'y']))
            if resid:
                csv_data[-1] = csv_data[-1] + ',%s' % resid[i]

        return '\n'.join(csv_data)


class PlotMachineLearning(Plot):
    """
    Generate plot for Machine Learning frames created in the MultiVariable Regression frame
    """
    def __init__(self, parent, options, multi_var_pred, multi_var_mse, x_variables, y_variable, mrn, study_date, uid,
                 ml_type=None, ml_type_short=None, **kwargs):
        """
        :param parent: the wx UI object where the plot will be displayed
        :param options: user preferences
        :type options: Options
        """
        Plot.__init__(self, parent, options)

        self.type = 'machine_learning'
        self.ml_type = ml_type
        self.ml_type_short = ml_type_short
        self.parent = parent
        self.mrn = mrn
        self.study_date = study_date
        self.uid = uid
        self.X, self.y = None, None

        self.size_factor = {'data': (0.38, 0.425),
                            'diff': (0.38, 0.425)}

        self.options = options
        self.multi_var_pred = multi_var_pred
        self.multi_var_mse = multi_var_mse

        self.y_variable = y_variable
        self.x_variables = x_variables

        self.div_title = {'train': Div(text="<b>Training Data</b>"),
                          'test': Div(text="<b>Testing Data</b>")}

        self.div_mse = {'train': Div(),
                        'test': Div()}

        self.source = {'train': {'data': ColumnDataSource(data=dict(x=[], y=[], mrn=[], study_date=[])),
                                 'predict': ColumnDataSource(data=dict(x=[], y=[], mrn=[], study_date=[])),
                                 'multi_var': ColumnDataSource(data=dict(x=[], y=[], mrn=[], study_date=[])),
                                 'diff': ColumnDataSource(data=dict(x=[], y_ml=[], y_mvr=[], y0=[], mrn=[],
                                                                    study_date=[])),
                                 'importance': ColumnDataSource(data=dict(x=[], top=[], width=[], variable=[]))},
                       'test': {'data': ColumnDataSource(data=dict(x=[], y=[], mrn=[], study_date=[])),
                                'predict': ColumnDataSource(data=dict(x=[], y=[], mrn=[], study_date=[])),
                                'multi_var': ColumnDataSource(data=dict(x=[], y=[], mrn=[], study_date=[])),
                                'diff': ColumnDataSource(data=dict(x=[], y_ml=[], y_mvr=[], y0=[], mrn=[],
                                                                   study_date=[]))}}

        self.figure.xaxis.axis_label = "Study"

        self.figures = {'train': {'data': figure(tools=DEFAULT_TOOLS),
                                  'diff': figure(tools=DEFAULT_TOOLS)},
                        'test': {'data': figure(tools=DEFAULT_TOOLS),
                                 'diff': figure(tools=DEFAULT_TOOLS)}}
        self.initialize_figures()

        self.__add_plot_data()
        self.__do_layout()
        self.__add_hover()
        self.__add_legend()

        self.set_figure_dimensions()
        self.update_bokeh_layout_in_wx_python()

    def __add_plot_data(self):
        self.glyphs = {}

        for data_type in {'train', 'test'}:
            srcs = self.source[data_type]
            figs = self.figures[data_type]
            opt = self.options
            self.glyphs[data_type] = {'data': figs['data'].cross('x', 'y', source=srcs['data'],
                                                                 color=opt.RANDOM_FOREST_COLOR_DATA, size=5),
                                      'predict': figs['data'].circle('x', 'y', source=srcs['predict'],
                                                                     color=opt.RANDOM_FOREST_COLOR_PREDICT,
                                                                     alpha=opt.RANDOM_FOREST_ALPHA),
                                      'multi_var': figs['data'].circle('x', 'y', source=srcs['multi_var'],
                                                                       color=opt.RANDOM_FOREST_COLOR_MULTI_VAR,
                                                                       alpha=opt.RANDOM_FOREST_ALPHA),
                                      'diff': figs['diff'].circle(x='x', y='y0', source=srcs['diff'], alpha=0),
                                      'diff_ml': figs['diff'].varea(x='x', y1='y_ml', y2='y0', source=srcs['diff'],
                                                                    color=opt.RANDOM_FOREST_COLOR_PREDICT,
                                                                    alpha=opt.RANDOM_FOREST_ALPHA * 0.7),
                                      'diff_mvr': figs['diff'].varea(x='x', y1='y_mvr', y2='y0', source=srcs['diff'],
                                                                     color=opt.RANDOM_FOREST_COLOR_MULTI_VAR,
                                                                     alpha=opt.RANDOM_FOREST_ALPHA * 0.7)}

    def __do_layout(self):
        self.bokeh_layout = row(column(self.div_title['train'], self.div_mse['train'],
                                       self.figures['train']['data'], self.figures['train']['diff']),
                                column(self.div_title['test'], self.div_mse['test'],
                                       self.figures['test']['data'], self.figures['test']['diff']))

    def __add_hover(self):
        for data_type in {'train', 'test'}:
            self.figures[data_type]['data'].add_tools(HoverTool(show_arrow=True,
                                                                tooltips=[('ID', '@mrn'),
                                                                          ('Date', '@study_date{%F}'),
                                                                          ('Study', '@x{int}'),
                                                                          ('Value', '@y{0.2f}')],
                                                                formatters={'study_date': 'datetime'}))

            self.figures[data_type]['diff'].add_tools(HoverTool(show_arrow=True, mode='vline',
                                                                tooltips=[('ID', '@mrn'),
                                                                          ('Date', '@study_date{%F}'),
                                                                          ('Study', '@x{int}'),
                                                                          (self.ml_type_short, '@y_ml{0.2f}'),
                                                                          ('MVR', '@y_mvr{0.2f}')],
                                                                formatters={'study_date': 'datetime'}))

    def __add_legend(self):
        legend = {}
        for data_type in {'train', 'test'}:
            legend[data_type] = {'data': Legend(items=[("Data  ", [self.glyphs[data_type]['data']]),
                                                       ("%s  " % self.ml_type, [self.glyphs[data_type]['predict']]),
                                                       ("Multi-Variable Reg.  ", [self.glyphs[data_type]['multi_var']])],
                                                orientation='horizontal'),
                                 'diff': Legend(items=[("%s  " % self.ml_type, [self.glyphs[data_type]['diff_ml']]),
                                                       ("Multi-Variable Reg.  ", [self.glyphs[data_type]['diff_mvr']])],
                                                orientation='horizontal')}
            for key in {'data', 'diff'}:
                self.figures[data_type][key].add_layout(legend[data_type][key], 'above')
                self.figures[data_type][key].legend.click_policy = "hide"

    def initialize_figures(self):

        for data_type in {'train', 'test'}:
            for key in {'data', 'diff'}:
                fig = self.figures[data_type][key]
                fig.xaxis.axis_label = 'Study'
                fig.xaxis.axis_label_text_font_size = self.options.PLOT_AXIS_LABEL_FONT_SIZE
                fig.yaxis.axis_label_text_font_size = self.options.PLOT_AXIS_LABEL_FONT_SIZE
                fig.xaxis.major_label_text_font_size = self.options.PLOT_AXIS_MAJOR_LABEL_FONT_SIZE
                fig.yaxis.major_label_text_font_size = self.options.PLOT_AXIS_MAJOR_LABEL_FONT_SIZE
                fig.min_border = self.options.MIN_BORDER
                fig.yaxis.axis_label_text_baseline = "bottom"
                if data_type == 'test':
                    fig.background_fill_color = "black"
                    fig.background_fill_alpha = 0.05

            self.figures[data_type]['data'].yaxis.axis_label = self.y_variable
            self.figures[data_type]['diff'].yaxis.axis_label = 'Residual'

        self.figures['train']['diff'].x_range = self.figures['train']['data'].x_range
        self.figures['test']['data'].y_range = self.figures['train']['data'].y_range
        self.figures['test']['diff'].y_range = self.figures['train']['diff'].y_range
        self.figures['test']['diff'].x_range = self.figures['test']['data'].x_range

    def set_figure_dimensions(self):
        panel_width, panel_height = self.parent.frame_size
        for data_type in {'train', 'test'}:
            for key in ['data', 'diff']:
                self.figures[data_type][key].plot_width = int(self.size_factor['data'][0] * float(panel_width))
                self.figures[data_type][key].plot_height = int(self.size_factor['data'][1] * float(panel_height))

    def update_data(self, plot_data):
        """
        :param plot_data:
        :type plot_data: MachineLearningPlotData
        :return:
        """

        self.X = plot_data.X['data']

        for data_type in {'train', 'test'}:
            x = plot_data.x[data_type]
            y = plot_data.y[data_type]
            y_pred = plot_data.predictions[data_type]
            multi_var_pred = [self.multi_var_pred[i] for i in plot_data.indices[data_type]]
            mrn = [self.mrn[i] for i in plot_data.indices[data_type]]
            uid = [self.uid[i] for i in plot_data.indices[data_type]]
            study_date = [self.study_date[i] for i in plot_data.indices[data_type]]

            srcs = self.source[data_type]

            srcs['data'].data = {'x': x, 'y': y, 'mrn': mrn, 'study_date': study_date, 'uid': uid}
            srcs['predict'].data = {'x': x, 'y': y_pred, 'mrn': mrn, 'study_date': study_date}
            srcs['multi_var'].data = {'x': x, 'y': multi_var_pred, 'mrn': mrn, 'study_date': study_date}
            srcs['diff'].data = {'x': x,
                                 'y_mvr': np.subtract(np.array(y), np.array(multi_var_pred)),
                                 'y_ml': np.subtract(np.array(y), np.array(y_pred)),
                                 'y0': [0] * len(x),
                                 'mrn': mrn, 'study_date': study_date}

            self.div_mse[data_type].text = "<u>Mean Square Error</u>: %0.2f (%s) --- %0.2f (MVR)" % \
                                           (plot_data.mse[data_type], self.ml_type_short, self.multi_var_mse)

        self.update_bokeh_layout_in_wx_python()

    def get_csv(self):

        col_titles = 'MRN,Study Instance UID,Study #,Date,%s,%s, Multi-Variable Regression' % \
                     (self.y_variable, self.ml_type)
        csv_data = []
        for data_type in {'train', 'test'}:
            data = self.source[data_type]['data'].data
            csv_data.append('%s Data\n%s' % (['Training', 'Testing'][data_type == 'test'], col_titles))
            for i in range(len(data['mrn'])):
                csv_data.append(','.join(str(data[key][i]).replace(',', '^')
                                         for key in ['mrn', 'uid', 'x', 'study_date', 'y']))
                csv_data[-1] = "%s,%s,%s" % (csv_data[-1],
                                             self.source[data_type]['predict'].data['y'][i],
                                             self.source[data_type]['multi_var'].data['y'][i])
            csv_data.append('\n')

        # Original dataset (in case not all data was used for training and testing
        # data = self.source['data'].data
        # csv_data.append('%s Data\n%s' % (['Training', 'Testing'][data_type == 'test'], col_titles))
        # for i in range(len(data['mrn'])):
        #     csv_data.append(','.join(str(data[key][i]).replace(',', '^')
        #                              for key in ['mrn', 'uid', 'x', 'study_date', 'y']))
        #     csv_data[-1] = "%s,%s,%s" % (csv_data[-1],
        #                                  self.source[data_type]['predict'].data['y'][i],
        #                                  self.source[data_type]['multi_var'].data['y'][i])

        return '\n'.join(csv_data)


class PlotFeatureImportance(Plot):
    def __init__(self, parent, options, x_variables, feature_importances, title):
        Plot.__init__(self, parent, options)

        self.size_factor = {'plot': (0.95, 0.9)}

        self.type = 'feature_importance'

        self.div_title = Div(text="<b>%s</b>" % title)

        self.parent = parent
        self.options = options

        self.x_variables = x_variables
        self.feature_importances = feature_importances

        self.source = {'plot': ColumnDataSource(data=dict(x=[], y=[], mrn=[], study_date=[]))}

        self.figure = figure(y_range=[''], tools="")

        self.initialize_figures()

        self.__add_plot_data()
        self.__do_layout()
        self.__add_hover()

        self.set_figure_dimensions()

        self.set_data()

        self.update_bokeh_layout_in_wx_python()

    def __add_plot_data(self):
        self.figure.hbar(y='y', right='right', left=0, height='height', source=self.source['plot'],
                         color=self.options.RANDOM_FOREST_COLOR_PREDICT, alpha=0.6)

    def __do_layout(self):
        self.bokeh_layout = column(self.div_title,
                                   self.figure)

    def __add_hover(self):
        self.figure.add_tools(HoverTool(show_arrow=True, mode='hline',
                                        tooltips=[('Importance', '@right{0.4f}'),
                                                  ('Variable', '@variable')]))

    def initialize_figures(self):
        self.figure.xaxis.axis_label_text_font_size = self.options.PLOT_AXIS_LABEL_FONT_SIZE
        self.figure.yaxis.axis_label_text_font_size = self.options.PLOT_AXIS_LABEL_FONT_SIZE
        self.figure.xaxis.major_label_text_font_size = self.options.PLOT_AXIS_MAJOR_LABEL_FONT_SIZE
        self.figure.yaxis.major_label_text_font_size = self.options.PLOT_AXIS_MAJOR_LABEL_FONT_SIZE
        self.figure.min_border = self.options.MIN_BORDER
        self.figure.xaxis.axis_label_text_baseline = "bottom"
        self.figure.xaxis.axis_label = 'Importance'

    def set_figure_dimensions(self):
        panel_width, panel_height = self.parent.GetSize()
        self.figure.plot_width = int(self.size_factor['plot'][0] * float(panel_width))
        self.figure.plot_height = int(self.size_factor['plot'][1] * float(panel_height))

    def set_data(self):
        length = len(self.feature_importances)
        order = [i[0] for i in sorted(enumerate(self.feature_importances), key=lambda x:x[1])]
        self.source['plot'].data = {'y': [i+0.5 for i in range(length)],
                                    'right': [self.feature_importances[i] for i in order],
                                    'height': [0.5] * length,
                                    'variable': [self.x_variables[i] for i in order]}
        self.figure.y_range.factors = [self.x_variables[i] for i in order]
        self.figure.x_range = Range1d(0, max(self.feature_importances) * 1.05)


class PlotROIMap(Plot):
    """
    Generate visual representation of the roi map
    """
    def __init__(self, parent, roi_map):
        """
        :param parent: the wx UI object where the plot will be displayed
        :param roi_map: roi map object
        :type roi_map: DatabaseROIs
        """
        Plot.__init__(self, parent, None)

        self.type = 'roi_map'
        self.parent = parent

        self.size_factor = {'plot': (0.972, 0.904)}

        self.roi_map = roi_map

        # Plot
        self.figure = figure(x_range=["Institutional ROI", "Physician ROI", "Variations"],
                             x_axis_location="above",
                             title="(Linked by Physician dropdowns)",
                             tools="reset, ywheel_zoom, ywheel_pan",
                             active_scroll='ywheel_pan')
        self.figure.title.align = 'center'
        # self.roi_map_plot.title.text_font_style = "italic"
        self.figure.title.text_font_size = "15pt"
        self.figure.xaxis.axis_line_color = None
        self.figure.xaxis.major_tick_line_color = None
        self.figure.xaxis.minor_tick_line_color = None
        self.figure.xaxis.major_label_text_font_size = "12pt"
        self.figure.xgrid.grid_line_color = None
        self.figure.ygrid.grid_line_color = None
        self.figure.yaxis.visible = False
        self.figure.outline_line_color = None
        self.figure.y_range = Range1d(-25, 0)
        self.figure.border_fill_color = "whitesmoke"
        self.figure.min_border_left = 50
        self.figure.min_border_bottom = 30

        self.source['map'] = ColumnDataSource(data={'name': [], 'color': [], 'x': [], 'y': [],
                                                    'x0': [], 'y0': [], 'x1': [], 'y1': []})
        self.figure.circle("x", "y", size=12, source=self.source['map'], line_color="black", fill_alpha=0.8,
                           color='color')
        labels = LabelSet(x="x", y="y", text="name", y_offset=8, text_color="#555555",
                          source=self.source['map'], text_align='center')
        self.figure.add_layout(labels)
        self.figure.segment(x0='x0', y0='y0', x1='x1', y1='y1', source=self.source['map'], alpha=0.5)

        self.bokeh_layout = column(self.figure)

    def update_roi_map_source_data(self, physician, plot_type=None):
        # TODO: allow ability to define initial viewing range
        self.set_figure_dimensions()
        new_data = self.roi_map.get_all_institutional_roi_visual_coordinates(physician)

        i_roi = new_data['institutional_roi']
        p_roi = new_data['physician_roi']
        b_roi = self.roi_map.branched_institutional_rois[physician]
        if plot_type == 'Linked':
            ignored_roi = [p_roi[i] for i in range(len(i_roi)) if i_roi[i] == 'uncategorized']
        elif plot_type == 'Unlinked':
            ignored_roi = [p_roi[i] for i in range(len(i_roi)) if i_roi[i] != 'uncategorized']
        elif plot_type == 'Branched':
            ignored_roi = [p_roi[i] for i in range(len(i_roi)) if i_roi[i] not in b_roi]
        else:
            ignored_roi = []

        new_data = self.roi_map.get_all_institutional_roi_visual_coordinates(physician,
                                                                             ignored_physician_rois=ignored_roi)

        self.figure.title.text = 'ROI Map for %s' % physician
        if new_data:
            self.source['map'].data = new_data
            self.figure.y_range.bounds = (min(self.source['map'].data['y']) - 3, max(self.source['map'].data['y']) + 3)
            self.update_bokeh_layout_in_wx_python()
        else:
            self.clear_source('map')
            self.clear_plot()

    def set_figure_dimensions(self):
        panel_width, panel_height = self.parent.GetSize()
        self.figure.plot_width = int(self.size_factor['plot'][0] * float(panel_width))
        self.figure.plot_height = int(self.size_factor['plot'][1] * float(panel_height))
