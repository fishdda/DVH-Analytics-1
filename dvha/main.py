#!/usr/bin/env python
# -*- coding: utf-8 -*-

# main.py
"""
The main file DVH Analytics
"""
# Copyright (c) 2016-2019 Dan Cutright
# This file is part of DVH Analytics, released under a BSD license.
#    See the file LICENSE included with this distribution, also
#    available at https://github.com/cutright/DVH-Analytics

import wx
from copy import deepcopy
from datetime import datetime
from dvha.db import sql_columns
from dvha.db.sql_to_python import QuerySQL
from dvha.db.sql_connector import echo_sql_db
from dvha.dialogs.main import query_dlg, UserSettings, About
from dvha.dialogs.database import SQLSettingsDialog
from dvha.dialogs.export import ExportCSVDialog, save_data_to_file
from dvha.models.import_dicom import ImportDicomFrame
from dvha.models.database_editor import DatabaseEditorFrame
from dvha.models.data_table import DataTable
from dvha.models.plot import PlotStatDVH
from dvha.models.dvh import DVH
from dvha.models.endpoint import EndpointFrame
from dvha.models.queried_data import QueriedDataFrame
from dvha.models.rad_bio import RadBioFrame
from dvha.models.time_series import TimeSeriesFrame
from dvha.models.regression import RegressionFrame
from dvha.models.control_chart import ControlChartFrame
from dvha.models.roi_map import ROIMapFrame
from dvha.options import Options
from dvha.paths import LOGO_PATH, DATA_DIR, ICONS
from dvha.tools.roi_name_manager import DatabaseROIs
from dvha.tools.stats import StatsData
from dvha.tools.utilities import get_study_instance_uids, scale_bitmap, is_windows, is_linux, get_window_size, \
    save_object_to_file, load_object_from_file, set_msw_background_color, initialize_directories_and_settings


class DVHAMainFrame(wx.Frame):
    def __init__(self, *args, **kwds):
        kwds["style"] = kwds.get("style", 0) | wx.DEFAULT_FRAME_STYLE
        wx.Frame.__init__(self, *args, **kwds)

        self.layout_set = False

        self.sizer_dvhs = wx.BoxSizer(wx.VERTICAL)

        set_msw_background_color(self)  # If windows, change the background color

        self.options = Options()

        # Initial DVH object and data
        self.dvh = None
        self.data = {key: None for key in ['Plans', 'Beams', 'Rxs']}
        self.stats_data = None
        self.save_data = {}

        self.toolbar_keys = ['Open', 'Close', 'Save', 'Export', 'Import', 'Database', 'ROI Map', 'Settings']
        self.toolbar_ids = {key: i + 1000 for i, key in enumerate(self.toolbar_keys)}

        # sql_columns.py contains dictionaries of all queryable variables along with their
        # SQL columns and tables. Numerical categories include their units as well.
        self.categorical_columns = sql_columns.categorical
        self.numerical_columns = sql_columns.numerical

        # Keep track of currently selected row in the query tables
        self.selected_index_categorical = None
        self.selected_index_numerical = None

        # Load ROI Map now and pass to other objects for continuity
        # TODO: Need a method to address multiple users editing roi_map at the same time
        self.roi_map = DatabaseROIs()

        self.__add_menubar()
        self.__add_tool_bar()
        self.__add_layout_objects()
        self.__bind_layout_objects()
        self.__set_properties()
        self.__set_tooltips()
        self.__add_notebook_frames()
        self.__do_layout()

        self.disable_query_buttons('categorical')
        self.disable_query_buttons('numerical')
        self.button_query_execute.Disable()
        self.__disable_notebook_tabs()

        columns = {'categorical': ['category_1', 'category_2', 'Filter Type'],
                   'numerical': ['category', 'min', 'max', 'Filter Type']}
        self.data_table_categorical = DataTable(self.table_categorical, columns=columns['categorical'])
        self.data_table_numerical = DataTable(self.table_numerical, columns=columns['numerical'])

        if not echo_sql_db():
            self.__disable_add_filter_buttons()

    def __add_tool_bar(self):
        self.frame_toolbar = wx.ToolBar(self, -1, style=wx.TB_HORIZONTAL | wx.TB_TEXT)
        self.SetToolBar(self.frame_toolbar)

        description = {'Open': "Open previously queried data",
                       'Close': "Clear queried data",
                       'Save': "Save queried data",
                       # 'Print': "Print a report",
                       'Export': "Export data to CSV",
                       'Import': "DICOM import wizard",
                       'Settings': "User Settings",
                       'Database': "Database Administrator Tools",
                       'ROI Map': "Define ROI name aliases"}

        for key in self.toolbar_keys:
            bitmap = wx.Bitmap(ICONS[key], wx.BITMAP_TYPE_ANY)
            if is_windows() or is_linux():
                bitmap = scale_bitmap(bitmap, 30, 30)
            self.frame_toolbar.AddTool(self.toolbar_ids[key], key, bitmap,
                                       wx.NullBitmap, wx.ITEM_NORMAL, description[key], "")

            if key in {'Close', 'Export', 'ROI Map'}:
                self.frame_toolbar.AddSeparator()

        self.Bind(wx.EVT_TOOL, self.on_save, id=self.toolbar_ids['Save'])
        self.Bind(wx.EVT_TOOL, self.on_open, id=self.toolbar_ids['Open'])
        self.Bind(wx.EVT_TOOL, self.on_export, id=self.toolbar_ids['Export'])
        self.Bind(wx.EVT_TOOL, self.on_toolbar_database, id=self.toolbar_ids['Database'])
        self.Bind(wx.EVT_TOOL, self.on_toolbar_settings, id=self.toolbar_ids['Settings'])
        self.Bind(wx.EVT_TOOL, self.on_toolbar_roi_map, id=self.toolbar_ids['ROI Map'])
        self.Bind(wx.EVT_TOOL, self.on_close, id=self.toolbar_ids['Close'])
        self.Bind(wx.EVT_TOOL, self.on_toolbar_import, id=self.toolbar_ids['Import'])

    def __add_menubar(self):

        self.frame_menubar = wx.MenuBar()

        file_menu = wx.Menu()
        # file_menu.Append(wx.ID_NEW, '&New')
        menu_open = file_menu.Append(wx.ID_OPEN, '&Open\tCtrl+O')
        menu_save = file_menu.Append(wx.ID_ANY, '&Save\tCtrl+S')
        menu_close = file_menu.Append(wx.ID_ANY, '&Close')

        export_plot = wx.Menu()
        export_dvhs = export_plot.Append(wx.ID_ANY, 'DVHs')
        export_time_series = export_plot.Append(wx.ID_ANY, 'Time Series')
        export_regression = export_plot.Append(wx.ID_ANY, 'Regression')
        export_control_chart = export_plot.Append(wx.ID_ANY, 'Control Chart')

        export = wx.Menu()
        export_csv = export.Append(wx.ID_ANY, 'Data to csv\tCtrl+E')
        export.AppendSubMenu(export_plot, 'Plot to html')
        file_menu.AppendSeparator()

        qmi = file_menu.Append(wx.ID_ANY, '&Quit\tCtrl+Q')

        self.data_menu = wx.Menu()

        self.data_views = {key: None for key in ['DVHs', 'Plans', 'Rxs', 'Beams']}
        self.data_menu.AppendSubMenu(export, '&Export')
        self.data_menu_items = {'DVHs': self.data_menu.Append(wx.ID_ANY, 'Show DVHs\tCtrl+1'),
                                'Plans': self.data_menu.Append(wx.ID_ANY, 'Show Plans\tCtrl+2'),
                                'Rxs': self.data_menu.Append(wx.ID_ANY, 'Show Rxs\tCtrl+3'),
                                'Beams': self.data_menu.Append(wx.ID_ANY, 'Show Beams\tCtrl+4')}

        settings_menu = wx.Menu()
        menu_pref = settings_menu.Append(wx.ID_PREFERENCES)
        menu_sql = settings_menu.Append(wx.ID_ANY, '&Database Connection\tCtrl+D')
        menu_user_settings = settings_menu.Append(wx.ID_ANY, '&User Settings\tCtrl+,')

        help_menu = wx.Menu()
        menu_about = help_menu.Append(wx.ID_ANY, '&About')

        self.Bind(wx.EVT_MENU, self.on_quit, qmi)
        self.Bind(wx.EVT_MENU, self.on_open, menu_open)
        self.Bind(wx.EVT_MENU, self.on_close, menu_close)
        self.Bind(wx.EVT_MENU, self.on_export, export_csv)
        self.Bind(wx.EVT_MENU, self.on_save, menu_save)
        self.Bind(wx.EVT_MENU, self.on_pref, menu_pref)
        self.Bind(wx.EVT_MENU, self.on_pref, menu_user_settings)
        self.Bind(wx.EVT_MENU, self.on_about, menu_about)
        self.Bind(wx.EVT_MENU, self.on_sql, menu_sql)

        self.Bind(wx.EVT_MENU, self.on_save_plot_dvhs, export_dvhs)
        self.Bind(wx.EVT_MENU, self.on_save_plot_time_series, export_time_series)
        self.Bind(wx.EVT_MENU, self.on_save_plot_regression, export_regression)
        self.Bind(wx.EVT_MENU, self.on_save_plot_control_chart, export_control_chart)
        self.Bind(wx.EVT_MENU, self.on_view_dvhs, self.data_menu_items['DVHs'])
        self.Bind(wx.EVT_MENU, self.on_view_plans, self.data_menu_items['Plans'])
        self.Bind(wx.EVT_MENU, self.on_view_rxs, self.data_menu_items['Rxs'])
        self.Bind(wx.EVT_MENU, self.on_view_beams, self.data_menu_items['Beams'])

        self.frame_menubar.Append(file_menu, '&File')
        self.frame_menubar.Append(self.data_menu, '&Data')
        self.frame_menubar.Append(settings_menu, '&Settings')
        self.frame_menubar.Append(help_menu, '&Help')
        self.SetMenuBar(self.frame_menubar)

    def __add_layout_objects(self):
        self.button_categorical = {'add': wx.Button(self, wx.ID_ANY, "Add Filter"),
                                   'del': wx.Button(self, wx.ID_ANY, "Delete Selected"),
                                   'edit': wx.Button(self, wx.ID_ANY, "Edit Selected")}
        self.button_numerical = {'add': wx.Button(self, wx.ID_ANY, "Add Filter"),
                                 'del': wx.Button(self, wx.ID_ANY, "Delete Selected"),
                                 'edit': wx.Button(self, wx.ID_ANY, "Edit Selected")}

        self.table_categorical = wx.ListCtrl(self, wx.ID_ANY, style=wx.BORDER_SUNKEN | wx.LC_REPORT)
        self.table_numerical = wx.ListCtrl(self, wx.ID_ANY, style=wx.BORDER_SUNKEN | wx.LC_REPORT)

        self.button_query_execute = wx.Button(self, wx.ID_ANY, "Query and Retrieve")

        self.notebook_main_view = wx.Notebook(self, wx.ID_ANY)
        self.tab_keys = ['Welcome', 'DVHs', 'Endpoints', 'Rad Bio', 'Time Series', 'Regression', 'Control Chart']
        self.notebook_tab = {key: wx.Panel(self.notebook_main_view, wx.ID_ANY) for key in self.tab_keys}

        self.text_summary = wx.StaticText(self, wx.ID_ANY, "", style=wx.ALIGN_LEFT)

    def __bind_layout_objects(self):
        self.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_item_select_categorical, self.table_categorical)
        self.Bind(wx.EVT_BUTTON, self.add_row_categorical, id=self.button_categorical['add'].GetId())
        self.Bind(wx.EVT_BUTTON, self.del_row_categorical, id=self.button_categorical['del'].GetId())
        self.Bind(wx.EVT_BUTTON, self.edit_row_categorical, id=self.button_categorical['edit'].GetId())
        self.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.doubleclick_categorical, self.table_categorical)

        self.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_item_select_numerical, self.table_numerical)
        self.Bind(wx.EVT_BUTTON, self.add_row_numerical, id=self.button_numerical['add'].GetId())
        self.Bind(wx.EVT_BUTTON, self.del_row_numerical, id=self.button_numerical['del'].GetId())
        self.Bind(wx.EVT_BUTTON, self.edit_row_numerical, id=self.button_numerical['edit'].GetId())
        self.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.doubleclick_numerical, self.table_numerical)

        self.Bind(wx.EVT_BUTTON, self.exec_query_button, id=self.button_query_execute.GetId())

        self.Bind(wx.EVT_SIZE, self.on_resize)

    def __set_properties(self):
        self.SetTitle("DVH Analytics")

        self.frame_toolbar.Realize()

        self.table_categorical.AppendColumn("Category1", format=wx.LIST_FORMAT_LEFT, width=180)
        self.table_categorical.AppendColumn("Category2", format=wx.LIST_FORMAT_LEFT, width=150)
        self.table_categorical.AppendColumn("Filter Type", format=wx.LIST_FORMAT_LEFT, width=80)

        self.table_numerical.AppendColumn("Category", format=wx.LIST_FORMAT_LEFT, width=150)
        self.table_numerical.AppendColumn("Min", format=wx.LIST_FORMAT_LEFT, width=90)
        self.table_numerical.AppendColumn("Max", format=wx.LIST_FORMAT_LEFT, width=90)
        self.table_numerical.AppendColumn("Filter Type", format=wx.LIST_FORMAT_LEFT, width=80)

    def __set_tooltips(self):
        self.button_categorical['add'].SetToolTip("Add a categorical data filter.")
        self.button_categorical['del'].SetToolTip("Delete the currently selected category filter.")
        self.button_categorical['edit'].SetToolTip("Edit the currently selected category filter.")
        self.button_numerical['add'].SetToolTip("Add a numerical data filter.")
        self.button_numerical['del'].SetToolTip("Delete the currently selected numerical data filter.")
        self.button_numerical['edit'].SetToolTip("Edit the currently selected data filter.")
        self.button_query_execute.SetToolTip("Query the database with the filters entered below. At least one "
                                             "filter must be added.")

    def __add_notebook_frames(self):
        self.plot = PlotStatDVH(self.notebook_tab['DVHs'], self.dvh, self.options)
        self.time_series = TimeSeriesFrame(self.notebook_tab['Time Series'], self.dvh, self.data, self.options)
        self.regression = RegressionFrame(self.notebook_tab['Regression'], self.stats_data, self.options)
        self.control_chart = ControlChartFrame(self.notebook_tab['Control Chart'], self.dvh, self.stats_data,
                                               self.options)
        self.radbio = RadBioFrame(self.notebook_tab['Rad Bio'], self.dvh, self.time_series, self.regression,
                                  self.control_chart)
        self.endpoint = EndpointFrame(self.notebook_tab['Endpoints'], self.dvh, self.time_series, self.regression,
                                      self.control_chart)

    def __do_layout(self):
        sizer_summary = wx.StaticBoxSizer(wx.StaticBox(self, wx.ID_ANY, "Summary"), wx.HORIZONTAL)
        sizer_query_numerical = wx.StaticBoxSizer(wx.StaticBox(self, wx.ID_ANY, "Query by Numerical Data"),
                                                  wx.VERTICAL)

        sizer_query_categorical = wx.StaticBoxSizer(wx.StaticBox(self, wx.ID_ANY, "Query by Categorical Data"),
                                                    wx.VERTICAL)

        sizer_categorical_buttons = wx.BoxSizer(wx.HORIZONTAL)
        sizer_numerical_buttons = wx.BoxSizer(wx.HORIZONTAL)

        for key in ['add', 'del', 'edit']:
            sizer_categorical_buttons.Add(self.button_categorical[key], 0, wx.ALL, 5)
            sizer_numerical_buttons.Add(self.button_numerical[key], 0, wx.ALL, 5)

        sizer_query_categorical.Add(sizer_categorical_buttons, 0, wx.ALL | wx.EXPAND, 5)
        sizer_query_categorical.Add(self.table_categorical, 1, wx.ALL | wx.EXPAND, 10)

        sizer_query_numerical.Add(sizer_numerical_buttons, 0, wx.ALL | wx.EXPAND, 5)
        sizer_query_numerical.Add(self.table_numerical, 1, wx.ALL | wx.EXPAND, 10)

        sizer_summary.Add(self.text_summary)

        panel_left = wx.BoxSizer(wx.VERTICAL)
        panel_left.Add(sizer_query_categorical, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.SHAPED | wx.TOP, 5)
        panel_left.Add(sizer_query_numerical, 0, wx.BOTTOM | wx.EXPAND | wx.LEFT | wx.RIGHT | wx.SHAPED, 5)
        panel_left.Add(self.button_query_execute, 0, wx.ALIGN_CENTER | wx.LEFT | wx.RIGHT, 15)
        panel_left.Add(sizer_summary, 1, wx.ALL | wx.EXPAND, 5)

        bitmap_logo = wx.StaticBitmap(self.notebook_tab['Welcome'], wx.ID_ANY,
                                      wx.Bitmap(LOGO_PATH, wx.BITMAP_TYPE_ANY))
        text_welcome = wx.StaticText(self.notebook_tab['Welcome'], wx.ID_ANY,
                                     "\n\nWelcome to DVH Analytics.\nIf you already have a database built, design "
                                     "a query with the filters to the left.\n\n\n\nDVH Analytics is a software "
                                     "application to help radiation oncology departments build an in-house database "
                                     "of treatment planning data for the purpose of historical comparisons and "
                                     "statistical analysis. This code is still in development. Please contact the "
                                     "developer if you are interested in testing or collaborating.\n\nThe application "
                                     "builds a SQL database of DVHs and various planning parameters from DICOM files "
                                     "(i.e., Plan, Structure, Dose). Since the data is extracted directly from DICOM "
                                     "files, we intend to accommodate an array of treatment planning system vendors.",
                                     style=wx.ALIGN_CENTER)

        text_welcome_size = get_window_size(0.417, 0.476)
        text_welcome.SetMinSize(text_welcome_size)
        text_welcome.Wrap(text_welcome_size[1])

        sizer_welcome = wx.BoxSizer(wx.VERTICAL)
        sizer_welcome.Add(bitmap_logo, 0, wx.ALIGN_CENTER | wx.LEFT | wx.RIGHT | wx.TOP, 100)
        sizer_welcome.Add(text_welcome, 0, wx.ALIGN_CENTER | wx.ALL, 25)
        self.notebook_tab['Welcome'].SetSizer(sizer_welcome)

        self.sizer_dvhs.Add(self.plot.layout, 1, wx.EXPAND | wx.ALL, 25)
        self.notebook_tab['DVHs'].SetSizer(self.sizer_dvhs)

        sizer_endpoint = wx.BoxSizer(wx.VERTICAL)
        sizer_endpoint.Add(self.endpoint.layout, 0, wx.ALL, 25)
        self.notebook_tab['Endpoints'].SetSizer(sizer_endpoint)

        sizer_rad_bio = wx.BoxSizer(wx.VERTICAL)
        sizer_rad_bio.Add(self.radbio.layout, 0, wx.ALL, 25)
        self.notebook_tab['Rad Bio'].SetSizer(sizer_rad_bio)

        sizer_time_series = wx.BoxSizer(wx.VERTICAL)
        sizer_time_series.Add(self.time_series.layout, 1, wx.EXPAND | wx.ALL, 25)
        self.notebook_tab['Time Series'].SetSizer(sizer_time_series)

        sizer_regression = wx.BoxSizer(wx.VERTICAL)
        sizer_regression.Add(self.regression.layout, 1, wx.EXPAND | wx.ALL, 25)
        self.notebook_tab['Regression'].SetSizer(sizer_regression)

        sizer_control_chart = wx.BoxSizer(wx.VERTICAL)
        sizer_control_chart.Add(self.control_chart.layout, 1, wx.EXPAND | wx.ALL, 25)
        self.notebook_tab['Control Chart'].SetSizer(sizer_control_chart)

        for key in self.tab_keys:
            self.notebook_main_view.AddPage(self.notebook_tab[key], key)

        sizer_main = wx.BoxSizer(wx.VERTICAL)
        sizer_main_wrapper = wx.BoxSizer(wx.HORIZONTAL)
        hbox_main = wx.BoxSizer(wx.HORIZONTAL)
        hbox_main.Add(panel_left, 0, wx.BOTTOM | wx.EXPAND | wx.LEFT | wx.TOP, 5)
        hbox_main.Add(self.notebook_main_view, 1, wx.BOTTOM | wx.EXPAND | wx.RIGHT | wx.TOP, 5)
        sizer_main_wrapper.Add(hbox_main, 1, wx.EXPAND, 0)
        sizer_main.Add(sizer_main_wrapper, 1, wx.EXPAND, 0)

        self.SetSizer(sizer_main)
        self.Layout()

        self.SetSize(get_window_size(0.833, 0.857))

        self.Center()

    def __enable_notebook_tabs(self):
        for key in self.tab_keys:
            if key in {'Regression', 'Control Chart'}:
                self.notebook_tab[key].Enable(self.dvh.count > 1)
            else:
                self.notebook_tab[key].Enable()
        self.__enable_initial_buttons_in_tabs()

    def __disable_notebook_tabs(self):
        for key in self.tab_keys:
            if key != 'Welcome':
                self.notebook_tab[key].Disable()

    def __enable_initial_buttons_in_tabs(self):
        self.endpoint.enable_initial_buttons()
        self.radbio.enable_initial_buttons()
        self.time_series.enable_initial_buttons()

    def __disable_add_filter_buttons(self):
        self.button_categorical['add'].Disable()
        self.button_numerical['add'].Disable()

    def __enable_add_filter_buttons(self):
        self.button_categorical['add'].Enable()
        self.button_numerical['add'].Enable()

    def enable_query_buttons(self, query_type):
        for key in ['del', 'edit']:
            {'categorical': self.button_categorical[key],
             'numerical': self.button_numerical[key]}[query_type].Enable()

    def disable_query_buttons(self, query_type):
        for key in ['del', 'edit']:
            {'categorical': self.button_categorical[key],
             'numerical': self.button_numerical[key]}[query_type].Disable()

    def update_all_query_buttons(self):
        tables = {'numerical': self.data_table_numerical, 'categorical': self.data_table_categorical}
        for key, table in tables.items():
            if table.data is not None:
                [self.disable_query_buttons, self.enable_query_buttons][table.row_count > 0](key)
            else:
                self.disable_query_buttons(key)

        if self.data_table_numerical.row_count + self.data_table_categorical.row_count > 0:
            self.button_query_execute.Enable()
        else:
            self.button_query_execute.Disable()

    # --------------------------------------------------------------------------------------------------------------
    # Menu bar event functions
    # --------------------------------------------------------------------------------------------------------------
    def on_save(self, evt):
        if self.save_data:
            dlg = wx.FileDialog(self, "Save your downloaded data to file", "", wildcard='*.dvha',
                                style=wx.FD_SAVE)
            dlg.SetDirectory(DATA_DIR)
            if dlg.ShowModal() == wx.ID_OK:
                self.save_data_obj()
                save_object_to_file(self.save_data, dlg.GetPath())
            dlg.Destroy()
        else:
            wx.MessageBox('There is no data to save. Please query/open some data first.', 'Save Error',
                          wx.OK | wx.OK_DEFAULT | wx.ICON_WARNING)

    def on_open(self, evt):
        dlg = wx.FileDialog(self, "Open saved data", "", wildcard='*.dvha',
                            style=wx.FD_FILE_MUST_EXIST | wx.FD_OPEN)
        if dlg.ShowModal() == wx.ID_OK:
            self.close()
            self.load_data_obj(dlg.GetPath())

        dlg.Destroy()

    def save_data_obj(self):
        self.save_data['dvh'] = self.dvh
        self.save_data['main_data'] = self.data
        self.save_data['time_stamp'] = datetime.now()
        self.save_data['version'] = self.options.VERSION
        # data_table_categorical and data_table_numerical saved after query to ensure these data reflect
        # the rest of the saved data
        self.save_data['endpoint'] = self.endpoint.get_save_data()
        self.save_data['time_series'] = self.time_series.get_save_data()
        self.save_data['radbio'] = self.radbio.get_save_data()
        self.save_data['regression'] = self.regression.get_save_data()

    def load_data_obj(self, abs_file_path):
        self.save_data = load_object_from_file(abs_file_path)
        self.dvh = self.save_data['dvh']
        self.data = self.save_data['main_data']

        # .load_save_data loses column widths?
        self.data_table_categorical.data = deepcopy(self.save_data['main_categorical']['data'])
        self.data_table_numerical.data = deepcopy(self.save_data['main_numerical']['data'])
        self.data_table_categorical.set_data_in_layout()
        self.data_table_numerical.set_data_in_layout()
        self.update_all_query_buttons()

        self.exec_query(load_saved_dvh_data=True)

        self.endpoint.load_save_data(self.save_data['endpoint'])
        if self.endpoint.has_data:
            self.endpoint.enable_buttons()

        self.radbio.load_save_data(self.save_data['radbio'])

        self.endpoint.update_endpoints_in_dvh()

        self.time_series.update_y_axis_options()
        self.time_series.load_save_data(self.save_data['time_series'])
        self.time_series.update_plot()

        if self.dvh.count > 1:
            self.regression.stats_data.update_endpoints_and_radbio()
            self.regression.update_combo_box_choices()
            self.regression.load_save_data(self.save_data['regression'])

            self.control_chart.update_combo_box_y_choices()

    def on_toolbar_settings(self, evt):
        self.on_pref()

    def on_toolbar_import(self, evt):
        self.check_db_then_call(ImportDicomFrame, self.roi_map, self.options)

    def on_toolbar_database(self, evt):
        self.check_db_then_call(DatabaseEditorFrame, self.roi_map)

    def on_toolbar_roi_map(self, evt):
        self.check_db_then_call(ROIMapFrame, self.roi_map)

    def check_db_then_call(self, func, *parameters):
        if not echo_sql_db():
            self.on_sql()

        if echo_sql_db():
            func(*parameters)
        else:
            wx.MessageBox('Connection to SQL database could not be established.', 'Connection Error',
                          wx.OK | wx.OK_DEFAULT | wx.ICON_WARNING)

    # --------------------------------------------------------------------------------------------------------------
    # Query event functions
    # --------------------------------------------------------------------------------------------------------------
    def on_item_select_categorical(self, evt):
        self.selected_index_categorical = self.table_categorical.GetFirstSelected()

    def on_item_select_numerical(self, evt):
        self.selected_index_numerical = self.table_numerical.GetFirstSelected()

    def add_row_categorical(self, evt):
        query_dlg(self, 'categorical')
        self.button_query_execute.Enable()
        self.update_all_query_buttons()

    def add_row_numerical(self, evt):
        query_dlg(self, 'numerical')
        self.update_all_query_buttons()

    def edit_row_categorical(self, *args):
        if self.selected_index_categorical is not None:
            query_dlg(self, 'categorical', set_values=True)

    def edit_row_numerical(self, *args):
        if self.selected_index_numerical is not None:
            query_dlg(self, 'numerical', set_values=True)

    def doubleclick_categorical(self, evt):
        self.selected_index_categorical = self.table_categorical.GetFirstSelected()
        self.edit_row_categorical()

    def doubleclick_numerical(self, evt):
        self.selected_index_numerical = self.table_numerical.GetFirstSelected()
        self.edit_row_numerical()

    def del_row_categorical(self, evt):
        self.data_table_categorical.delete_row(self.selected_index_categorical)
        self.selected_index_categorical = None
        self.update_all_query_buttons()

    def del_row_numerical(self, evt):
        self.data_table_numerical.delete_row(self.selected_index_numerical)
        self.selected_index_numerical = None
        self.update_all_query_buttons()

    def exec_query_button(self, evt):
        # TODO: Thread this process
        self.exec_query()

    def exec_query(self, load_saved_dvh_data=False):
        wait = wx.BusyCursor()

        # self.dvh = None
        self.plot.clear_plot()
        self.endpoint.clear_data()
        self.time_series.clear_data()
        self.time_series.initialize_y_axis_options()
        self.regression.clear()
        self.control_chart.clear_data()
        self.control_chart.initialize_y_axis_options()
        self.radbio.clear_data()

        if not load_saved_dvh_data:
            uids, dvh_str = self.get_query()
            self.dvh = DVH(dvh_condition=dvh_str, uid=uids)

        if self.dvh.count:
            self.endpoint.update_dvh(self.dvh)
            self.text_summary.SetLabelText(self.dvh.get_summary())
            self.plot.update_plot(self.dvh)
            del wait
            self.notebook_main_view.SetSelection(1)
            self.update_data(load_saved_dvh_data=load_saved_dvh_data)
            self.time_series.update_data(self.dvh, self.data)
            if self.dvh.count > 1:
                self.control_chart.update_data(self.dvh, self.stats_data)

            self.radbio.update_dvh_data(self.dvh)

            self.__enable_notebook_tabs()

            self.save_data['main_categorical'] = self.data_table_categorical.get_save_data()
            self.save_data['main_numerical'] = self.data_table_numerical.get_save_data()
        else:
            del wait
            wx.MessageBox('No DVHs returned. Please modify query or import more data.', 'Query Error',
                          wx.OK | wx.OK_DEFAULT | wx.ICON_WARNING)
            self.dvh = None

    def get_query(self):

        # Used to accumulate lists of query strings for each table
        # Will assume each item in list is complete query for that SQL column
        queries = {'Plans': [], 'Rxs': [], 'Beams': [], 'DVHs': []}

        # Used to group queries by variable, will combine all queries of same variable with an OR operator
        # e.g., queries_by_sql_column['Plans'][key] = list of strings, where key is sql column
        queries_by_sql_column = {'Plans': {}, 'Rxs': {}, 'Beams': {}, 'DVHs': {}}

        # Categorical filter
        if self.data_table_categorical.row_count:
            for i, category in enumerate(self.data_table_categorical.data['category_1']):
                table = self.categorical_columns[category]['table']
                col = self.categorical_columns[category]['var_name']
                value = self.data_table_categorical.data['category_2'][i]
                if col not in queries_by_sql_column[table]:
                    queries_by_sql_column[table][col] = []
                operator = ['=', '!='][{'Include': 0, 'Exclude': 1}[self.data_table_categorical.data['Filter Type'][i]]]
                queries_by_sql_column[table][col].append("%s %s '%s'" % (col, operator, value))

        # Range filter
        if self.data_table_numerical.row_count:
            for i, category in enumerate(self.data_table_numerical.data['category']):
                table = self.numerical_columns[category]['table']
                col = self.numerical_columns[category]['var_name']
                value_low = self.data_table_numerical.data['min'][i]
                value_high = self.data_table_numerical.data['max'][i]
                if 'date' in col:
                    value_low = "'%s'::date" % value_low
                    value_high = "'%s'::date" % value_high
                if col not in queries_by_sql_column[table]:
                    queries_by_sql_column[table][col] = []
                operator = ['BETWEEN', 'NOT BETWEEN'][
                    {'Include': 0, 'Exclude': 1}[self.data_table_numerical.data['Filter Type'][i]]]
                queries_by_sql_column[table][col].append("%s %s %s AND %s" % (col, operator, value_low, value_high))

        for table in queries:
            for col in queries_by_sql_column[table]:
                queries[table].append("(%s)" % ' OR '.join(queries_by_sql_column[table][col]))
            queries[table] = ' AND '.join(queries[table])

        uids = get_study_instance_uids(plans=queries['Plans'], rxs=queries['Rxs'], beams=queries['Beams'])['common']

        return uids, queries['DVHs']

    def update_data(self, load_saved_dvh_data=False):
        wait = wx.BusyCursor()
        tables = ['Plans', 'Rxs', 'Beams']
        if hasattr(self.dvh, 'study_instance_uid'):
            if not load_saved_dvh_data:
                condition_str = "study_instance_uid in ('%s')" % "','".join(self.dvh.study_instance_uid)
                self.data = {key: QuerySQL(key, condition_str) for key in tables}
        else:
            self.data = {key: None for key in tables}
        del wait

        if hasattr(self.dvh, 'study_instance_uid'):
            wait = wx.BusyCursor()
            self.stats_data = StatsData(self.dvh, self.data)
            self.regression.stats_data = self.stats_data
            self.control_chart.stats_data = self.stats_data
            try:
                self.regression.update_combo_box_choices()
            except ValueError:
                # TODO: Print error in GUI
                pass
            self.control_chart.update_combo_box_y_choices()
            del wait

    # --------------------------------------------------------------------------------------------------------------
    # Menu bar event functions
    # --------------------------------------------------------------------------------------------------------------
    def on_quit(self, evt):
        self.Close()

    def on_close(self, evt):
        if self.dvh:
            dlg = wx.MessageDialog(self, "Clear all data and plots?", caption='Close',
                                   style=wx.YES | wx.NO | wx.NO_DEFAULT | wx.CENTER | wx.ICON_EXCLAMATION)
            dlg.Center()
            res = dlg.ShowModal()
            if res == wx.ID_YES:
                self.close()
            dlg.Destroy()

    def close(self):
        self.dvh = None
        self.data_table_categorical.delete_all_rows()
        self.data_table_numerical.delete_all_rows()
        self.plot.clear_plot()
        self.endpoint.clear_data()
        self.radbio.clear_data()
        self.time_series.clear_data()
        self.notebook_main_view.SetSelection(0)
        self.text_summary.SetLabelText("")
        self.__disable_notebook_tabs()
        self.disable_query_buttons('categorical')
        self.disable_query_buttons('numerical')
        self.button_query_execute.Disable()
        self.time_series.initialize_y_axis_options()
        self.regression.clear()
        self.control_chart.initialize_y_axis_options()
        self.control_chart.plot.clear_plot()

    def on_export(self, evt):
        if self.dvh is not None:
            ExportCSVDialog(self)
        else:
            wx.MessageBox('There is no data to export! Please query some data first.', 'Export Error',
                          wx.OK | wx.ICON_WARNING)

    def on_about(self, evt):
        About()

    def on_pref(self, *args):
        UserSettings(self.options)

    def on_sql(self, *args):
        SQLSettingsDialog()
        [self.__disable_add_filter_buttons, self.__enable_add_filter_buttons][echo_sql_db()]()

    def on_save_plot_dvhs(self, evt):
        save_data_to_file(self, 'Save DVHs plot', self.plot.html_str,
                          wildcard="HTML files (*.html)|*.html")

    def on_save_plot_time_series(self, evt):
        save_data_to_file(self, 'Save Time Series plot', self.time_series.plot.html_str,
                          wildcard="HTML files (*.html)|*.html")

    def on_save_plot_regression(self, evt):
        save_data_to_file(self, 'Save Regression plot', self.regression.plot.html_str,
                          wildcard="HTML files (*.html)|*.html")

    def on_save_plot_control_chart(self, evt):
        save_data_to_file(self, 'Save Control Chart plot', self.control_chart.plot.html_str,
                          wildcard="HTML files (*.html)|*.html")

    def on_view_dvhs(self, evt):
        self.view_table_data('DVHs')

    def on_view_plans(self, evt):
        self.view_table_data('Plans')

    def on_view_rxs(self, evt):
        self.view_table_data('Rxs')

    def on_view_beams(self, evt):
        self.view_table_data('Beams')

    def view_table_data(self, key):
        if key == 'DVHs':
            data = self.dvh
        else:
            data = self.data[key]

        if data:
            if self.get_menu_item_status(key) == 'Show':
                self.data_views[key] = QueriedDataFrame(data, key, self.data_menu, self.data_menu_items[key].GetId())
            else:
                self.data_views[key].on_close()
                self.data_views[key] = None
        else:
            dlg = wx.MessageDialog(self, 'Please query/open some data first.', 'ERROR!', wx.ICON_ERROR | wx.OK_DEFAULT)
            dlg.ShowModal()
            dlg.Destroy()

    def get_menu_item_status(self, key):
        show_hide = ['Hide', 'Show']['Show' in self.data_menu.GetLabel(self.data_menu_items[key].GetId())]
        return show_hide

    def redraw_plots(self):
        if self.dvh:
            self.plot.redraw_plot()
            self.time_series.plot.redraw_plot()
            self.regression.plot.redraw_plot()
            self.control_chart.plot.redraw_plot()

    def on_resize(self, *evt):
        try:
            self.Refresh()
            self.Layout()
            wx.CallAfter(self.redraw_plots)
        except RuntimeError:
            pass


class MainApp(wx.App):
    def OnInit(self):

        initialize_directories_and_settings()
        if is_windows():
            from dvha.tools.windows_reg_edit import set_ie_emulation_level
            set_ie_emulation_level()

        self.SetAppName('DVH Analytics')
        self.frame = DVHAMainFrame(None, wx.ID_ANY, "")
        self.SetTopWindow(self.frame)
        self.frame.Show()
        return True


def start():
    app = MainApp(0)
    app.MainLoop()
