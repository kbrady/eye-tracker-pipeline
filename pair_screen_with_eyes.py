# to find files and parse paths correctly
import os
# to look in the right places
import settings
# to get the session info
from video_to_frames import Session
# to save output
import csv
# to store rows
from collections import namedtuple
# to assess how long things are taking
import time
# to read xml files
from bs4 import BeautifulSoup

# a class to store and interpret boxes
class BBox:
	def __init__(self, info):
		if type(info) == str or type(info) == unicode:
			info = [float(x) for x in info.split(' ')]
		self.right, self.top, self.left, self.bottom = info

# A parent object for lines and words which defines some shared functionality
class Part(object):
	def __init__(self, tag):
		self.bbox = BBox(tag['bbox'])

# An object to interpret words in hocr files
class Word(Part):
	def __init__(self, tag):
		super(self.__class__, self).__init__(tag)
		# set text and clean up by changing all text to ascii (assuming we are working in English for the moment)
		self.text = tag.get_text()

	def __repr__(self):
		return self.text

	def get_distance(self, x, y):
		x_distance = min(abs(self.bbox.left - x), abs(self.bbox.right - x))
		y_distance = min(abs(self.bbox.top - y), abs(self.bbox.bottom - y))
		return (x_distance ** 2 + y_distance ** 2) ** .5

class Line(Part):
	def __init__(self, tag):
		super(self.__class__, self).__init__(tag)
		self.children = [Word(sub_tag) for sub_tag in tag.find_all('word')]

	def __repr__(self):
		# We need to make sure to exclude empty words.
		# The system has no method for filtering them and they are likely to occure with tessaract
		return ' '.join([str(word) for word in self.children if len(str(word)) > 0])

	def get_distances(self, x, y):
		output = []
		for word in self.children:
			distance = word.get_distance(x,y)
			# if more than one 'word' got assigned to the same box then we need to double count that distance
			output += [distance] * len(str(word).split(' '))
		return output

class Document:
	def __init__(self, xml_filepath, dr_time):
		# save the transition time (in seconds) for this document so it is easy to refer to
		self.time = dr_time
		# save the location of the original file
		self.xml_filepath = xml_filepath
		with open(xml_filepath, 'r') as input_file:
			data = ' '.join([line for line in input_file])
			soup = BeautifulSoup(data, "html.parser")
			tag = soup.find('root')
			self.correct_filepath = 'correct_text' + os.sep + tag['filename']
			self.lines = [Line(sub_tag) for sub_tag in tag.find_all('line')]

	def get_word_distance(self, row):
		list_of_line_strings = [str(l) for l in self.lines]
		x, y = get_x_y(row)
		list_of_distances = []
		with open(self.correct_filepath, 'r') as word_file:
			for line in word_file:
				line = line.strip()
				if x == -1 or y == -1 or line not in list_of_line_strings:
					list_of_distances += [None] * len(line.split(' '))
					continue
				line_index = list_of_line_strings.index(line)
				list_of_distances += self.lines[line_index].get_distances(x,y)
		return list_of_distances

	def get_words(self):
		with open(self.correct_filepath, 'r') as word_file:
			return [word for line in word_file for word in line.strip().split(' ')]

# the set of documents for each frame set
class Corpus:
	def __init__(self, sess):
		# get the filepath for each document
		def time_to_filepath(dr_time):
			xml_dir = sess.dir_name + os.sep + settings.xml_dir
			minutes = int(dr_time/60)
			seconds = dr_time - (minutes * 60)
			filename = num_to_str(minutes)+'-'+num_to_str(seconds)+'.xml'
			return xml_dir + os.sep + filename
		digital_reading_times = [x for x in sess.metadata if x['part'] == 'digital reading']
		# for now let us assume that there is only one digital reading
		digital_reading_transitions = digital_reading_times[0]['transitions']
		self.documents = [Document(time_to_filepath(dr_time), dr_time) for dr_time in digital_reading_transitions]
		self.dir_name = sess.dir_name

	# best to assign rows all together since we can capitalize off the fact that the rows and documents are both
	# ordered by time
	def assign_rows(self, row_list):
		assignment = []
		document_index = 0
		for row in row_list:
			dr_time = get_time(row)
			while document_index < len(self.documents) - 1 and self.documents[document_index + 1].time - .05 <= dr_time:
				document_index += 1
			assignment.append((row, document_index))
		return assignment

	def calculate_row(self, row, document_index):
		document = self.documents[document_index]
		word_assignment = document.get_word_distance(row)
		dr_time = get_time(row)
		x, y = get_x_y(row)
		clock_time = row.Timestamp
		document_id = document.xml_filepath.split(os.sep)[-1]
		return [x, y, dr_time, clock_time, document_id] + word_assignment

	def interpret_rows(self, row_list):
		document_assignment = self.assign_rows(row_list)
		with open(self.dir_name + os.sep + settings.eye_tracking_word_csv_file, 'w') as csvfile:
			writer = csv.writer(csvfile, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
			header = ['GazeX', 'GazeY', 'SessionTime', 'ClockTime', 'FrameTime'] + self.documents[0].get_words()
			writer.writerow(header)
			for row, document_index in document_assignment:
				writer.writerow(self.calculate_row(row, document_index))

# this is used for the filenames
# it ads a 0 before the digit of numbers less than 10
def num_to_str(num):
	if num < 10:
		return '0'+str(num)
	return str(num)

def get_time(row):
	return float(row.TimeSignal)/1000

def get_x_y(row):
	def int_or_none(val):
		return None if len(val) == 0 else int(val)
	return int_or_none(row.GazeX), int_or_none(row.GazeY)

def get_eye_tracking_rows(sess):
	digital_reading_times = [x for x in sess.metadata if x['part'] == 'digital reading']
	# for now let us assume that there is only one digital reading
	digital_reading_start = digital_reading_times[0]['start_time']
	digital_reading_end = digital_reading_times[0]['end_time']
	digital_reading_rows = []
	with open(sess.sensor_data_filename, 'r') as csvfile:
		eye_tracking_reader = csv.reader(csvfile, delimiter='\t', quotechar='"')
		RowObj = None
		for row in eye_tracking_reader:
			if len(row) < 3:
				continue
			if RowObj is None:
				RowObj = namedtuple('RowObj', row)
				continue
			row = RowObj(*tuple(row))
			if get_time(row) < digital_reading_start:
				continue
			if get_time(row) > digital_reading_end:
				break
			digital_reading_rows.append(row)
	return digital_reading_rows

def pair_eye_tracking_data(sess):
	corpus = Corpus(sess)
	row_list = get_eye_tracking_rows(sess)
	corpus.interpret_rows(row_list)

if __name__ == '__main__':
	# some session ids from the pilot data
	pilot_sessions = ['seventh_participant', 'fifth_participant', 'third_student_participant', 'first_student_participant_second_take', 'first_student_participant', 'Amanda', 'eighth_participant', 'sixth_participant', 'fourth-participant-second-version' , 'fourth_participant', 'second_student_participant']
	
	t0 = time.time()
	for sess_name in pilot_sessions:
		sess = Session(sess_name)
		pair_eye_tracking_data(sess)
		break
	t1 = time.time()
	print 'time taken', t1 - t0