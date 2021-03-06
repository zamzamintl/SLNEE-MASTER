# Part of Odoo. See LICENSE file for full copyright and licensing details.

import math
import logging
import datetime
from dateutil.relativedelta import relativedelta
from dateutil.rrule import (MONTHLY, WEEKLY)
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT, DEFAULT_SERVER_DATETIME_FORMAT

_logger = logging.getLogger(__name__)


class Sheet(models.Model):
    _name = 'hr_timesheet.sheet'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'portal.mixin']
    _table = 'hr_timesheet_sheet'
    _order = "id desc"
    _description = 'Timesheet Sheet'

    def _default_date_start(self):
        """
            Default Get start date From configuration setting.
        """
        user = self.env['res.users'].browse(self.env.uid)
        r = user.company_id and user.company_id.sheet_range or WEEKLY
        today = fields.Date.from_string(fields.Date.context_today(self))
        if r == WEEKLY:
            return today - relativedelta(days=today.weekday())
        elif r == MONTHLY:
            return today + relativedelta(day=1)
        return today

    def _default_date_end(self):
        """
            Default Get end date From configuration setting.
        """
        user = self.env['res.users'].browse(self.env.uid)
        r = user.company_id and user.company_id.sheet_range or WEEKLY
        today = fields.Date.from_string(fields.Date.context_today(self))
        if r == WEEKLY:
            return today + relativedelta(days=6-today.weekday())
        elif r == MONTHLY:
            return today + relativedelta(months=1, day=1, days=-1)
        return today

    def _default_employee(self):
        """
            Default Get employee from current user.
        """
        emp_ids = self.env['hr.employee'].search(
            [('user_id', '=', self.env.uid)])
        return emp_ids and emp_ids[0] or False

    name = fields.Char(
        string="Note",
        states={'confirm': [('readonly', True)], 'done': [('readonly', True)]},
    )
    employee_id = fields.Many2one(
        comodel_name='hr.employee',
        string='Employee',
        default=lambda self: self._default_employee(),
        required=True,
    )
    user_id = fields.Many2one(
        comodel_name='res.users',
        related='employee_id.user_id',
        string='User',
        store=True,
        readonly=True,
    )
    date_start = fields.Date(
        string='Date From',
        default=lambda self: self._default_date_start(),
        required=True,
        index=True,
        states={'draft': [('readonly', False)]},
    )
    date_end = fields.Date(
        string='Date To',
        default=lambda self: self._default_date_end(),
        required=True,
        index=True,
        states={'draft': [('readonly', False)]},
    )
    timesheet_ids = fields.One2many(
        comodel_name='account.analytic.line',
        inverse_name='sheet_id',
        string='Timesheets',
        readonly=True,
        states={
            'draft': [('readonly', False)],
        }
    )
    line_ids = fields.One2many(
        comodel_name='hr_timesheet.sheet.line',
        compute='_compute_line_ids',
        string='Timesheets',
        states={
            'draft': [('readonly', False)],
        }
    )
    state = fields.Selection([
        ('draft', 'Open'),
        ('confirm', 'Waiting Approval'),
        ('done', 'Approved')],
        default='draft', track_visibility='onchange',
        string='Status', required=True, readonly=True, index=True,
    )
    company_id = fields.Many2one(
        comodel_name='res.company',
        string='Company',
        default=lambda self: self.env['res.company']._company_default_get(),
    )
    department_id = fields.Many2one(
        comodel_name='hr.department',
        string='Department',
    )
    add_line_project_id = fields.Many2one(
        comodel_name='project.project',
        string='Select Project',
        help='If selected, the associated project is added '
             'to the timesheet sheet when clicked the button.',
        states={
            'draft': [('readonly', False)],
        },
    )
    add_line_task_id = fields.Many2one(
        comodel_name='project.task',
        string='Select Task',
        help='If selected, the associated task is added '
             'to the timesheet sheet when clicked the button.',
        states={
            'draft': [('readonly', False)],
        },
    )

    #---------start attendance----------
    attendances_ids = fields.One2many('hr.attendance', 'sheet_id', string='Attendances')
    total_attendance = fields.Float(string='Total Attendance',readonly=True)
    total_timesheet = fields.Float(string='Total Timesheet',readonly=True)
    total_difference = fields.Float(string='Difference',readonly=True)
    total_overtime = fields.Float(string='Total Overtime',readonly=True)
    period_ids = fields.One2many('hr_timesheet_sheet_sheet_day', 'sheet_id', string='Period', readonly=True)
    attendance_count = fields.Integer(compute='_compute_attendances', string="Attendances")

    @api.multi
    def compute_timesheet(self):
        """
            compute attendance sheet
        """
        self.period_ids.unlink()
        start =  datetime.datetime.strptime(self.date_start, DEFAULT_SERVER_DATE_FORMAT).date()
        end =  datetime.datetime.strptime(self.date_end, DEFAULT_SERVER_DATE_FORMAT).date()
        date_array = (start + datetime.timedelta(days=x) for x in range(0, (end-start).days+1))
        for date_object in date_array:
            self.write({
                        'period_ids': [(0,0, {
                            'name': date_object.strftime("%Y-%m-%d"),
                            'total_overtime':0,
                            'total_attendance':0,
                            'total_timesheet':0,
                            })],
                        })
        for period_id in self.period_ids:
            period_id.reason = ''
            active_contract = self.employee_id.get_active_contracts(date=self.date_start)
            date_period = datetime.datetime.strptime(period_id.name, DEFAULT_SERVER_DATE_FORMAT).date()
            timesheets = self.env['account.analytic.line'].search([('employee_id', '=', self.employee_id.id), ('date', '=', period_id.name)])
            for timesheet in timesheets:
                period_id.total_timesheet += timesheet.unit_amount
            overtime_hours = []
            for attendance in self.attendances_ids:
                attendance._get_attendance_duration()
                check_in = datetime.datetime.strptime(attendance.check_in, DEFAULT_SERVER_DATETIME_FORMAT).date()
                if check_in == date_period:
                    overtime_hours.append(attendance.outside_calendar_duration)
                    # period_id.total_overtime += attendance.outside_calendar_duration
                    period_id.total_attendance += attendance.duration
            if active_contract:
                if active_contract.calculate_overtime and active_contract.overtime_limit and overtime_hours:
                    period_id.total_overtime = active_contract.overtime_limit if sum(overtime_hours) > active_contract.overtime_limit else sum(overtime_hours)
                elif active_contract.calculate_overtime and not active_contract.overtime_limit:
                    period_id.total_overtime = sum(overtime_hours)
            period_id.total_difference = (period_id.total_attendance - period_id.total_timesheet)
            weekday_end = (date_period + relativedelta(weekday=6)).strftime('%Y-%m-%d')
            public_holidays = self.env['hr.holidays.public.line'].search([('start_date', '>=', period_id.name), ('end_date', '<=', period_id.name)])
            if public_holidays:
                period_id.reason += ', '.join([holiday.name for holiday in public_holidays])
            for week in self.employee_id.resource_calendar_id.weekend_ids:
                if week.code - 1 == date_period.weekday():
                    period_id.reason += 'Weekend '
            hr_holidays = self.env['hr.holidays'].search([('employee_id', '=', self.employee_id.id), ('state', '=', 'validate'), ('date_from', '<=', date_period.strftime("%Y-%m-%d")), ('date_to', '>=', date_period.strftime("%Y-%m-%d"))])
            if hr_holidays:
                period_id.reason += ', '.join([holiday.holiday_status_id.name for holiday in hr_holidays])
                period_id.holiday_ids = [(6, 0, hr_holidays.ids)]
        self.total_attendance = sum(line.total_attendance for line in self.period_ids)
        self.total_difference = sum(line.total_difference for line in self.period_ids)
        self.total_timesheet = sum(line.total_timesheet for line in self.period_ids)
        self.total_overtime = sum(line.total_overtime for line in self.period_ids)

    @api.multi
    def buttton_timesheet(self):
        """
            display employee's timesheet
        """
        account_ids = self.env['account.analytic.line'].search([('employee_id', '=', self.employee_id.id)])
        action = self.env.ref('hr_timesheet.act_hr_timesheet_line')
        result = action.read()[0]
        if len(account_ids) > 1:
            result['domain'] = [('id','in',account_ids.ids)]
        elif len(account_ids) == 1:
            res = self.env.ref('hr_timesheet.hr_timesheet_line_form', False)
            result['views'] = [(res.id, 'form')]
            result['res_id'] = account_ids[0].id
        else:
           result['domain'] = [('id','in',account_ids.ids)]
        return result

    @api.multi
    def action_sheet_report(self):
        """
            display attendance in employee between start date to end date
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'HR Timesheet/Attendance Report',
            'res_model': 'hr.timesheet.attendance.report',
            'domain': [('date', '>=', self.date_start), ('date', '<=', self.date_end)],
            'view_mode': 'pivot',
            'context': {'search_default_user_id': self.user_id.id, }
        }

    @api.depends('attendances_ids')
    def _compute_attendances(self):
        """
            count total attendancesbetween start date to end date
        """
        for sheet in self:
            sheet.attendance_count = len(sheet.attendances_ids)

    #------------end attandance----------

    @api.constrains('date_start', 'date_end')
    def _check_start_end_dates(self):
        """
            Check the Start Date must be lower than End Date.
        """
        for sheet in self:
            if sheet.date_start > sheet.date_end:
                raise ValidationError(
                    _('The start date cannot be later than the end date.'))

    @api.constrains('date_start', 'date_end', 'employee_id')
    def _check_sheet_date(self, forced_user_id=False):
        """
            Check the sheets cannot overlap .
        """
        for sheet in self:
            new_user_id = forced_user_id or sheet.user_id and sheet.user_id.id
            if new_user_id:
                self.env.cr.execute(
                    """
                    SELECT id
                    FROM hr_timesheet_sheet
                    WHERE (date_start <= %s and %s <= date_end)
                        AND user_id=%s
                        AND company_id=%s
                        AND id <> %s""",
                    (sheet.date_end, sheet.date_start, new_user_id,
                     sheet.company_id.id, sheet.id))
                if any(self.env.cr.fetchall()):
                    raise ValidationError(
                        _('You cannot have 2 sheets that overlap!\n'
                          'Please use the menu \'Timesheet Sheet\' '
                          'to avoid this problem.'))

    @api.multi
    @api.constrains('company_id', 'employee_id')
    def _check_company_id_employee_id(self):
        """
            Check the Company in the Timesheet Sheet and in Employee must be the same.
        """
        for rec in self.sudo():
            if rec.company_id and rec.employee_id.company_id and \
                    rec.company_id != rec.employee_id.company_id:
                raise ValidationError(
                    _('The Company in the Timesheet Sheet and in '
                      'the Employee must be the same.'))

    @api.multi
    @api.constrains('company_id', 'department_id')
    def _check_company_id_department_id(self):
        """
            Check the Company in the Timesheet Sheet and in Department must be the same.
        """
        for rec in self.sudo():
            if rec.company_id and rec.department_id.company_id and \
                    rec.company_id != rec.department_id.company_id:
                raise ValidationError(
                    _('The Company in the Timesheet Sheet and in '
                      'the Department must be the same.'))

    @api.multi
    @api.constrains('company_id', 'add_line_project_id')
    def _check_company_id_add_line_project_id(self):
        """
            Check the Company in the Timesheet Sheet and in Project must be the same.
        """
        for rec in self.sudo():
            if rec.company_id and rec.add_line_project_id.company_id and \
                    rec.company_id != rec.add_line_project_id.company_id:
                raise ValidationError(
                    _('The Company in the Timesheet Sheet and in '
                      'the Project must be the same.'))

    @api.multi
    @api.constrains('company_id', 'add_line_task_id')
    def _check_company_id_add_line_task_id(self):
        """
            Check the Company in the Timesheet Sheet and in Task must be the same.
        """
        for rec in self.sudo():
            if rec.company_id and rec.add_line_task_id.company_id and \
                    rec.company_id != rec.add_line_task_id.company_id:
                raise ValidationError(
                    _('The Company in the Timesheet Sheet and in '
                      'the Task must be the same.'))

    @api.constrains('company_id')
    def _check_company_id(self):
        """
            Check the Company as this is assigned Company.
        """
        for rec in self.sudo():
            if not rec.company_id:
                continue
            for field in rec.timesheet_ids:
                if rec.company_id and field.company_id and \
                        rec.company_id != field.company_id:
                    raise ValidationError(_(
                        'You cannot change the company, as this %s (%s) '
                        'is assigned to %s (%s).'
                    ) % (rec._name, rec.display_name,
                         field._name, field.display_name))

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        """
            onchange the value based on selected employee department
        """
        if self.employee_id:
            self.department_id = self.employee_id.department_id
            self.user_id = self.employee_id.user_id

    @api.multi
    def _compute_line_ids(self):
        """
            get line ids
        """
        for sheet in self:
            if not all([sheet.date_start, sheet.date_end]):
                continue
            dates = sheet._get_dates()
            if not dates:
                continue
            timesheets = self.env['account.analytic.line'].search([
                ('project_id', '!=', False),
                ('date', '<=', sheet.date_end),
                ('date', '>=', sheet.date_start),
                ('employee_id', '=', sheet.employee_id.id),
                ('sheet_id', 'in', [sheet and sheet.id or False, False]),
                ('company_id', '=', sheet.company_id.id),
            ])
            lines = self.env['hr_timesheet.sheet.line']
            for date in dates:
                for project in timesheets.mapped('project_id'):
                    timesheet = timesheets.filtered(
                        lambda x: (x.project_id == project))
                    tasks = [task for task in timesheet.mapped('task_id')]
                    if not timesheet or not all(
                            [t.task_id for t in timesheet]):
                        tasks += [self.env['project.task']]
                    for task in tasks:
                        lines |= self.env['hr_timesheet.sheet.line'].create(
                            sheet._get_default_analytic_line(
                                date=date,
                                project=project,
                                task=task,
                                timesheet=timesheet.filtered(
                                    lambda t: date == t.date
                                    and t.task_id.id == task.id),
                            ))
            sheet.line_ids = lines

    @api.onchange('date_start', 'date_end', 'timesheet_ids')
    def _onchange_dates_or_timesheets(self):
        """
            onchange the value based on selected start date, end date and timesheet.
        """
        self._compute_line_ids()

    @api.onchange('add_line_project_id')
    def onchange_add_project_id(self):
        """
            Load the project to the timesheet sheet
        """
        if self.add_line_project_id:
            return {
                'domain': {
                    'add_line_task_id': [
                        ('project_id', '=', self.add_line_project_id.id),
                        ('company_id', '=', self.company_id.id),
                        ('id', 'not in',
                         self.timesheet_ids.mapped('task_id').ids)],
                },
            }
        else:
            return {
                'domain': {
                    'add_line_task_id': [('id', '=', False)],
                },
            }

    @api.multi
    def copy(self, default=None):
        raise UserError(_('You cannot duplicate a sheet.'))

    @api.model
    def create(self, vals):
        if 'employee_id' in vals:
            if not self.env['hr.employee'].browse(vals['employee_id']).user_id:
                raise UserError(
                    _('In order to create a sheet for this employee, '
                      'you must link him/her to an user.'))
        res = super(Sheet, self).create(vals)
        res.write({'state': 'draft'})
        self.delete_empty_lines(True)
        return res

    @api.multi
    def write(self, vals):
        if 'employee_id' in vals:
            new_user_id = self.env['hr.employee'].\
                browse(vals['employee_id']).user_id.id
            if not new_user_id:
                raise UserError(
                    _('In order to create a sheet for this employee, '
                      'you must link him/her to an user.'))
            self._check_sheet_date(forced_user_id=new_user_id)
        res = super(Sheet, self).write(vals)
        if self.state == 'draft':
            for rec in self:
                rec.delete_empty_lines(True)
        return res

    @api.multi
    def name_get(self):
        # week number according to ISO 8601 Calendar
        return [(r['id'], _('Week ') + str(fields.Date.from_string(
            r['date_start']).isocalendar()[1]))
            for r in self.sudo().read(['date_start'], load='_classic_write')]
        # It's a cheesy name because you may have ranges different of weeks.

    @api.multi
    def unlink(self):
        sheets = self.read(['state'])
        for sheet in sheets:
            if sheet['state'] in ('confirm', 'done'):
                raise UserError(
                    _('You cannot delete a timesheet sheet '
                      'which is already confirmed.'))
        analytic_timesheet_toremove = self.env['account.analytic.line']
        for sheet in self:
            analytic_timesheet_toremove += \
                sheet.timesheet_ids.filtered(lambda t: not t.task_id)
        analytic_timesheet_toremove.unlink()
        return super(Sheet, self).unlink()

    @api.multi
    def action_timesheet_draft(self):
        if not self.env.user.has_group('hr_timesheet.group_hr_timesheet_user'):
            raise UserError(
                _('Only an HR Officer or Manager can refuse sheets '
                  'or reset them to draft.'))
        self.write({'state': 'draft'})
        return True

    @api.multi
    def action_timesheet_confirm(self):
        for sheet in self:
            if sheet.employee_id and sheet.employee_id.parent_id \
                    and sheet.employee_id.parent_id.user_id:
                self.message_subscribe_users(
                    user_ids=[sheet.employee_id.parent_id.user_id.id])
            if sheet.add_line_task_id:
                sheet.add_line_task_id = False
            if sheet.add_line_project_id:
                sheet.add_line_project_id = False
        self.write({'state': 'confirm'})
        return True

    @api.multi
    def action_timesheet_done(self):
        if not self.env.user.has_group('hr_timesheet.group_hr_timesheet_user'):
            raise UserError(
                _('Only an HR Officer or Manager can approve sheets.'))
        if self.filtered(lambda sheet: sheet.state != 'confirm'):
            raise UserError(_("Cannot approve a non-submitted sheet."))
        self.write({'state': 'done'})

    @api.multi
    def button_add_line(self):
        for rec in self:
            if rec.state == 'draft':
                rec.add_line()
                rec.add_line_task_id = False
                rec.add_line_project_id = False
        return True

    def _get_date_name(self, date):
        return fields.Date.from_string(date).strftime("%a\n%b %d")

    def _get_dates(self):
        start = fields.Date.from_string(self.date_start)
        end = fields.Date.from_string(self.date_end)
        if end < start:
            return []
        # time_period = end - start
        # number_of_days = time_period/timedelta(days=1)
        dates = [fields.Date.to_string(start)]
        while start != end:
            start += relativedelta(days=1)
            dates.append(fields.Date.to_string(start))
        return dates

    def _get_line_name(self, project, task=None):
        name = '{}'.format(project.name)
        if task:
            name += ' - {}'.format(task.name)
        return name

    def _get_default_analytic_line(self, date, project, task, timesheet=None):
        name_y = self._get_line_name(project, task)
        timesheet = self.clean_timesheets(timesheet)
        values = {
            'value_x': self._get_date_name(date),
            'value_y': name_y,
            'date': date,
            'project_id': project.id,
            'task_id': task and task.id or False,
            'count_timesheets': len(timesheet),
            'unit_amount': 0.0,
        }
        if self.id:
            values.update({
                'sheet_id': self.id,
            })
        if timesheet:
            amount = sum([t.unit_amount for t in timesheet])
            values.update({
                'unit_amount': amount,
            })
        return values

    @api.model
    def _prepare_empty_analytic_line(self):
        return {
            'name': '/',
            'employee_id': self.employee_id.id,
            'date': self.date_start,
            'project_id': self.add_line_project_id and
            self.add_line_project_id.id or False,
            'task_id': self.add_line_task_id and
            self.add_line_task_id.id or False,
            'sheet_id': self.id,
            'unit_amount': 0.0,
            'company_id': self.company_id.id,
        }

    @api.model
    def add_line(self):
        if self.add_line_project_id:
            values = self._prepare_empty_analytic_line()
            name_line = self._get_line_name(
                self.add_line_project_id, self.add_line_task_id)
            if self.line_ids.mapped('value_y'):
                self.delete_empty_lines(False)
            if name_line not in self.line_ids.mapped('value_y'):
                self.timesheet_ids |= \
                    self.env['account.analytic.line'].create(values)

    def clean_timesheets(self, timesheet):
        if self.id:
            for aal in timesheet.filtered(lambda a: not a.sheet_id):
                aal.write({'sheet_id': self.id})
        repeated = timesheet.filtered(lambda t: t.name == "/")
        if len(repeated) > 1:
            timesheet = repeated.merge_timesheets()
        return timesheet

    def delete_empty_lines(self, allow_empty_rows=False):
        for name in self.line_ids.mapped('value_y'):
            row = self.line_ids.filtered(lambda l: l.value_y == name)
            if row:
                task = row[0].task_id and row[0].task_id.id or False
                ts_row = self.env['account.analytic.line'].search([
                    ('project_id', '=', row[0].project_id.id),
                    ('task_id', '=', task),
                    ('date', '<=', self.date_end),
                    ('date', '>=', self.date_start),
                    ('employee_id', '=', self.employee_id.id),
                    ('sheet_id', '=', self.id),
                    ('company_id', '=', self.company_id.id),
                ])
                if allow_empty_rows and self.add_line_project_id:
                    check = any([l.unit_amount for l in row])
                else:
                    check = not all([l.unit_amount for l in row])
                if check:
                    ts_row.filtered(
                        lambda t: t.name == '/' and not t.unit_amount).unlink()

    # ------------------------------------------------
    # OpenChatter methods and notifications
    # ------------------------------------------------

    @api.multi
    def _track_subtype(self, init_values):
        if self:
            record = self[0]
            if 'state' in init_values and record.state == 'confirm':
                return 'hr_timesheet_sheet.mt_timesheet_confirmed'
            elif 'state' in init_values and record.state == 'done':
                return 'hr_timesheet_sheet.mt_timesheet_approved'
        return super(Sheet, self)._track_subtype(init_values)

    @api.model
    def _needaction_domain_get(self):
        empids = self.env['hr.employee'].search(
            [('parent_id.user_id', '=', self.env.uid)])
        if not empids:
            return False
        return ['&', ('state', '=', 'confirm'),
                ('employee_id', 'in', empids.ids)]


class SheetLine(models.TransientModel):
    _name = 'hr_timesheet.sheet.line'
    _description = 'Timesheet Sheet Line'

    sheet_id = fields.Many2one(
        comodel_name='hr_timesheet.sheet',
        ondelete='cascade',
    )
    date = fields.Date(
        string='Date',
    )
    project_id = fields.Many2one(
        comodel_name='project.project',
        string='Project',
    )
    task_id = fields.Many2one(
        comodel_name='project.task',
        string='Task',
    )
    value_x = fields.Char(
        string='Date Name',
    )
    value_y = fields.Char(
        string='Project Name',
    )
    unit_amount = fields.Float(
        string="Quantity",
        default=0.0,
    )
    count_timesheets = fields.Integer(
        default=0,
    )

    @api.onchange('unit_amount')
    def onchange_unit_amount(self):
        """This method is called when filling a cell of the matrix.
        It checks if there exists timesheets associated  to that cell.
        If yes, it does several comparisons to see if the unit_amount of
        the timesheets should be updated accordingly."""
        self.ensure_one()
        if self.unit_amount < 0.0:
            self.write({'unit_amount': 0.0})
        if self.unit_amount and not self.count_timesheets:
            self._create_timesheet(self.unit_amount)
        elif self.count_timesheets:
            task = self.task_id and self.task_id.id or False
            timesheets = self.env['account.analytic.line'].search([
                ('project_id', '=', self.project_id.id),
                ('task_id', '=', task),
                ('date', '=', self.date),
                ('employee_id', '=', self.sheet_id.employee_id.id),
                ('sheet_id', '=', self.sheet_id.id),
                ('company_id', '=', self.sheet_id.company_id.id),
            ])
            if len(timesheets) != self.count_timesheets:
                _logger.info('Found timesheets %s, expected %s',
                             len(timesheets), self.count_timesheets)
                self.count_timesheets = len(timesheets)
            if not self.unit_amount:
                new_ts = timesheets.filtered(lambda t: t.name == '/')
                other_ts = timesheets.filtered(lambda t: t.name != '/')
                if new_ts:
                    new_ts.unlink()
                for timesheet in other_ts:
                    timesheet.write({'unit_amount': 0.0})
                self.count_timesheets = len(other_ts)
            else:
                if self.count_timesheets == 1:
                    timesheets.write({'unit_amount': self.unit_amount})
                elif self.count_timesheets > 1:
                    amount = sum([t.unit_amount for t in timesheets])
                    new_ts = timesheets.filtered(lambda t: t.name == '/')
                    other_ts = timesheets.filtered(lambda t: t.name != '/')
                    diff_amount = self.unit_amount - amount
                    if new_ts:
                        if len(new_ts) > 1:
                            new_ts = new_ts.merge_timesheets()
                            self.count_timesheets = len(
                                self.sheet_id.timesheet_ids)
                        if new_ts.unit_amount + diff_amount >= 0.0:
                            new_ts.unit_amount += diff_amount
                            if not new_ts.unit_amount:
                                new_ts.unlink()
                                self.count_timesheets -= 1
                        else:
                            amount = self.unit_amount - new_ts.unit_amount
                            new_ts.write({'unit_amount': 0.0})
                            new_ts.unlink()
                            self.count_timesheets -= 1
                            self._diff_amount_timesheets(amount, other_ts)
                    else:
                        if diff_amount > 0.0:
                            self._create_timesheet(diff_amount)
                        else:
                            amount = self.unit_amount
                            self._diff_amount_timesheets(amount, other_ts)
                else:
                    raise ValidationError(
                        _('Error code: Cannot have 0 timesheets.'))

    def _create_timesheet(self, amount):
        values = self._line_to_timesheet(amount)
        if self.env['account.analytic.line'].create(values):
            self.count_timesheets += 1

    @api.model
    def _diff_amount_timesheets(self, amount, timesheets):
        for timesheet in timesheets:
            diff_amount = timesheet.unit_amount - amount
            if diff_amount >= 0.0:
                timesheet.unit_amount = diff_amount
                break
            else:
                amount -= timesheet.unit_amount
                timesheet.write({'unit_amount': 0.0})

    @api.model
    def _line_to_timesheet(self, amount):
        task = self.task_id.id if self.task_id else False
        return {
            'name': '/',
            'employee_id': self.sheet_id.employee_id.id,
            'date': self.date,
            'project_id': self.project_id.id,
            'task_id': task,
            'sheet_id': self.sheet_id.id,
            'unit_amount': amount,
            'company_id': self.sheet_id.company_id.id,
        }


class hr_timesheet_sheet_sheet_day(models.Model):
    _name = "hr_timesheet_sheet_sheet_day"
    _description = "Timesheets by Period"
    _order = 'name'

    name = fields.Date('Date', readonly=True)
    sheet_id = fields.Many2one('hr_timesheet.sheet', string='Sheet', readonly=True, index=True)
    total_timesheet = fields.Float(string='Total Timesheet', readonly=True)
    total_attendance = fields.Float(string='Attendance', readonly=True)
    total_difference = fields.Float(string='Difference', readonly=True)
    total_overtime = fields.Float(string='Overtime', readonly=True)
    reason = fields.Char(string='Reason', readonly=True)
    holiday_ids = fields.Many2many('hr.holidays', string="Leave Request")


class HRAttendance(models.Model):
    _inherit = "hr.attendance"

    sheet_id_computed = fields.Many2one('hr_timesheet.sheet', string='Sheet', compute='_compute_sheet', index=True, ondelete='cascade',
        search='_search_sheet')
    sheet_id = fields.Many2one('hr_timesheet.sheet', compute='_compute_sheet', string='Sheet', store=True)

    @api.depends('employee_id', 'check_in', 'check_out', 'sheet_id_computed.date_end', 'sheet_id_computed.date_start', 'sheet_id_computed.employee_id')
    def _compute_sheet(self):
        """Links the attendance to the corresponding sheet
        """
        for attendance in self:
            corresponding_sheet = self.env['hr_timesheet.sheet'].search(
                [('date_end', '>=', attendance.check_in), ('date_start', '<=', attendance.check_in),
                 ('employee_id', '=', attendance.employee_id.id),
                 ('state', 'in', ['draft', 'new'])], limit=1)
            if corresponding_sheet:
                attendance.sheet_id_computed = corresponding_sheet[0]
                attendance.sheet_id = corresponding_sheet[0]

    def _search_sheet(self, operator, value):
        assert operator == 'in'
        ids = []
        for ts in self.env['hr_timesheet.sheet'].browse(value):
            self._cr.execute("""
                    SELECT a.id
                        FROM hr_attendance a
                    WHERE %(date_end)s >= a.check_in
                        AND %(date_start)s <= a.check_in
                        AND %(employee_id)s = a.employee_id
                    GROUP BY a.id""", {'date_start': ts.date_start,
                                       'date_end': ts.date_end,
                                       'employee_id': ts.employee_id.id, })
            ids.extend([row[0] for row in self._cr.fetchall()])
        return [('id', 'in', ids)]
