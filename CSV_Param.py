import csv
import os

def CSV_Proj_Params(proj_name):
    CSV_File_Name = 'All_Projects.csv'
    """
    Loads database configuration from All_Projects.csv for a specific project folder.
    """
    base_path = os.path.dirname(__file__)
    csv_path = os.path.join(base_path, CSV_File_Name)
    config = {}
    try:
        with open(csv_path, mode='r', newline='') as file:
            reader = csv.DictReader(file)
            for row in reader:
                if row['Project_Name'] == proj_name:
                    config = {
                        'Web_Suffix': row['Web_Suffix'],
                        'Folder_Name': row['Folder_Name'],
                        'Button_Text': row['Button_Text'],
                        'MYSQL_HOST': row['MYSQL_HOST'],
                        'MYSQL_PORT': int(row['MYSQL_PORT']),
                        'MYSQL_USER': row['MYSQL_USER'],
                        'MYSQL_PASSWORD': row['MYSQL_PASSWORD'],
                        'MYSQL_DB': row['MYSQL_DB'],
                        'MYSQL_CURSORCLASS': row['MYSQL_CURSORCLASS']
                    }
                    break
    except FileNotFoundError:
        print(f"Error: {CSV_File_Name} not found at {base_path}")
    except Exception as e:
        print(f"Error loading DB config: {e}")
    return config
