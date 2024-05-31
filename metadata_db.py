'''
metadata_db is a simple interface to the camtrawl metadata database.
'''

import os
from PyQt5 import QtCore, QtSql


class metadata_db(QtCore.QObject):

    def __init__(self, parent=None):

        super(metadata_db, self).__init__(parent)

        self.db = QtSql.QSqlDatabase.addDatabase("QSQLITE")
        self.is_open = False


    def open(self, db_file):

        db_file = os.path.normpath(db_file)
        self.db.setDatabaseName(db_file)

        if self.db.open():
            #  check if this is a new or existing database file
            if (not 'cameras' in self.db.tables()):
                #  we'll assume if the cameras table doesn't exist, then this is a new
                #  database file. Create the base camtrawl acquisition tables
                self.create_database()
            self.is_open = True
        else:
            self.is_open = False

        return self.is_open


    def update_camera(self, name, device_id, serial, label, rot, version, speed):
        '''
        update_camera updates this camera's info in the cameras table. The camera is
        added if it doesn't exist in the table
        '''

        has_camera = False
        sql = "SELECT camera from cameras"
        query = QtSql.QSqlQuery(sql, self.db)
        while query.next():
            if (query.value(0) == name):
                has_camera = True
                break

        if has_camera:
            sql = ("UPDATE cameras SET camera='" + name + "', device_id='" + device_id + "', serial_number='" +
                    serial + "', label='" + label + "', rotation='" + rot + "', device_version='" +
                    version + "', device_speed='" + speed + "'")
        else:
            sql = ("INSERT INTO cameras VALUES('" + name + "','" + device_id + "','" +
                    serial + "','" + label + "','" + rot + "','" + version + "','" + speed + "')")
        query = QtSql.QSqlQuery(sql, self.db)
        query.exec_()


    def insert_async_data(self, sensor_id, header, rx_time, data):
        '''
        insert_async_data inserts a row in the async_data table
        '''

        time_str = self.datetime_to_db_str(rx_time)
        sql = ("INSERT INTO async_data VALUES('" + time_str + "','" + sensor_id + "','" + header +
                "','" + data + "')" )
        query = QtSql.QSqlQuery(sql, self.db)
        query.exec_()


    def insert_sync_data(self, image_num, rx_time, sensor_id, header, data):
        '''
        insert_sync_data inserts a row in the sensor_data table
        '''

        time_str = self.datetime_to_db_str(rx_time)
        sql = ("INSERT INTO sensor_data VALUES(" + str(image_num) + ",'" + time_str + "','" +
                sensor_id + "','" + header + "','" + data + "')")
        query = QtSql.QSqlQuery(sql, self.db)
        query.exec_()


    def get_next_image_number(self):
        '''
        get_next_image_number queries the maximum image number from the
        images table and returns the next number in the sequence.
        '''


        sql = "SELECT MAX(number) FROM images"
        query = QtSql.QSqlQuery(sql, self.db)
        query.first()
        if query.value(0) is None or query.value(0) == '':
            next_img_num = 1
        else:
            next_img_num = query.value(0) + 1

        return next_img_num


    def add_dropped(self, image_num, cam_name, trig_time):
        '''
        add_dropped inserts an entry in the dropped images table
        '''

        time_str = self.datetime_to_db_str(trig_time)
        sql = ("INSERT INTO dropped VALUES(" + str(image_num) + ",'" + cam_name + "','" + time_str + "')")
        query = QtSql.QSqlQuery(sql, self.db)
        query.exec_()


    def add_image(self, image_num, cam_name, trig_time, image_filename, exposure,
            gain, save_still, save_frame, discarded=None, md5=None):

        if not md5:
            md5 = 'NULL'
        else:
            md5 = "'" + md5 + "'"
        if not discarded:
            discarded = 'NULL'
        else:
            discarded = 1

        #  convert bools to ints
        save_still = int(save_still)
        save_frame = int(save_frame)

        time_str = self.datetime_to_db_str(trig_time)
        sql = ("INSERT INTO images VALUES(" + str(image_num) + ",'" + cam_name + "','" + time_str + "','" +
                image_filename + "'," + str(exposure) + "," + str(gain) + "," + str(save_still) + ',' +
                str(save_frame) + ',' + str(discarded) + ',' + md5 + ")")
        query = QtSql.QSqlQuery(sql, self.db)
        query.exec_()


    def set_metadata_info(self, vessel_name, survey_name, camera_name, description, deployment_time):

        time_str = self.datetime_to_db_str(deployment_time)
        sql = ("INSERT INTO deployment (survey_name,vessel_name,camera_name,survey_description,deployment_time) " +
                "VALUES ('" + survey_name + "','" + vessel_name + "','" + camera_name + "','" +
                description + "','" + time_str + "')")

        query = QtSql.QSqlQuery(sql, self.db)
        query.exec_()


    def set_image_extension(self, extension):

        sql = ("INSERT INTO deployment_data (deployment_parameter,parameter_value) " +
                "VALUES ('image_file_type','" + extension + "')")
        query = QtSql.QSqlQuery(sql, self.db)
        query.exec_()


    def set_video_extension(self, extension):

        sql = ("INSERT INTO deployment_data (deployment_parameter,parameter_value) " +
                "VALUES ('video_file_type','" + extension + "')")
        query = QtSql.QSqlQuery(sql, self.db)
        query.exec_()


    def datetime_to_db_str(self, dt_obj):

        dt_string = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
        dt_string = dt_string + '.%03d' % (round(dt_obj.microsecond / 1000))

        return dt_string


    def close(self):
        self.db.close()
        self.is_open = False


    def create_database(self):

        # list of SQL statements that define the base camtrawlMetadata database schema
        sql = ["CREATE TABLE cameras (camera TEXT NOT NULL, device_id TEXT, serial_number TEXT, label TEXT, rotation TEXT, device_version TEXT, device_speed TEXT, PRIMARY KEY(camera))",
               "CREATE TABLE images (number INTEGER NOT NULL, camera TEXT NOT NULL, time TEXT, name TEXT, exposure_us INTEGER, gain FLOAT, still_image INTEGER, video_frame INTEGER, discarded INTEGER, md5_checksum TEXT, PRIMARY KEY(number,camera))",
               "CREATE TABLE dropped (number INTEGER NOT NULL, camera TEXT NOT_NULL, time TEXT, PRIMARY KEY(number,camera))",
               "CREATE TABLE sensor_data (number INTEGER NOT NULL, time TEXT NOT NULL, sensor_id TEXT NOT NULL, header TEXT NOT NULL, data TEXT, PRIMARY KEY(number,time,sensor_id,header))",
               "CREATE TABLE async_data (time TEXT NOT NULL, sensor_id TEXT NOT NULL, header TEXT NOT NULL, data TEXT, PRIMARY KEY(time,sensor_id,header))",
               "CREATE TABLE deployment_data (deployment_parameter TEXT NOT NULL, parameter_value TEXT NOT NULL, PRIMARY KEY(deployment_parameter))",
               "CREATE TABLE deployment (deployment_name TEXT, survey_name TEXT, vessel_name TEXT, camera_name TEXT, survey_description TEXT, deployment_time TEXT, deployment_latitude NUMBER, deployment_longitude NUMBER, max_deployment_depth NUMBER, comments TEXT"]

        #  execute the sql statements
        for s in sql:
            query = QtSql.QSqlQuery(s, self.db)
            query.exec_()
