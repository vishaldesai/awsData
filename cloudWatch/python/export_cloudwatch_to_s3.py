#############################################################################################################################################################################
##### This script is used export cloudwatch logs into S3 bucket in batch mode                                                                                             ###
##### Only one export task can run at a time                                                                                                                              ###
##### https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/cloudwatch_limits_cwl.html                                                                                 ###
#############################################################################################################################################################################
##CHANGE LOG:                                                                                                                                                             ###
## Author     Date        Change Description                                                                                                                              ###
## #########  ##########  ###################################################################################################################################################
## Vishal     2018-08-25  Initial version.                                                                                                                                ###
#############################################################################################################################################################################
## Prerequisite:                                                                                                                                                          ###
## 1. Create dynamodb table with batchid string (partition key), epoch_timestamp number (sort key), processed string                                                      ###
## 2. Add record in dynamodb table with partition key and timestamp from where you want to start copying logs from. If you dont poplulate dyanmodb table                  ###
##    it will process logs 1 hour before current timestamp                                                                                                                ###
#############################################################################################################################################################################
## Usage:python export_cloudwatch_to_s3.py --region us-east-1 --ddb cloudwatch6 --ddbstream cloudwatchtod0 --secondstoprocess 10800 --destinationbucket mytestbucket      ###
#############################################################################################################################################################################


import boto3
from boto3.dynamodb.conditions import Key, Attr
import time
import datetime
import os
from botocore.exceptions import ClientError
import sys
import argparse

# Client connection
def query_table(table_name,stream,region):
    dynamodb = boto3.client('dynamodb', region_name=region)
    try:

        response = dynamodb.query(TableName=table_name, KeyConditionExpression="batchid = :X",
                                  ExpressionAttributeValues={":X": {"S": stream}}, ScanIndexForward=True)
        if response['Count'] > 0:
            max_timestamp = response['Items'][response['Count']-1]['epoch_timestamp']['N']
        else:
            max_timestamp = int(time.time()) - 3600
        return max_timestamp
    except ClientError as e:
        print(e.response)

def insert_table(table_name, stream,end_epoch_timestamp,region):
    dynamodb = boto3.client('dynamodb', region_name=region)
    try:
        response = dynamodb.put_item(
            TableName=table_name,
            Item={
                'batchid': {'S' : stream },
                'epoch_timestamp': { 'N' : str(end_epoch_timestamp) },
                'processed': { 'S' : 'Y' }
                }
            )
        return response
    except ClientError as e:
        print(e.response)

def main(arguments):
    
    # Argument parser
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--region', required=True)
    parser.add_argument('--ddb', required=True)
    parser.add_argument('--ddbstream', required=True)
    parser.add_argument('--secondstoprocess', required=True)
    parser.add_argument('--destinationbucket',required=True)
    args = parser.parse_args(arguments)

    # Define client for log groups
    logsclient = boto3.client('logs', region_name=args.region)

    # Compute epoch timestamps to process
    last_epoch_timestamp=int(query_table(args.ddb,args.ddbstream,args.region))
    start_time = time.time()

    print("Timestamp returned by dyanmdb : " + str(last_epoch_timestamp) + ' - ' + str(datetime.datetime.fromtimestamp(last_epoch_timestamp).strftime('%m/%d/%Y %H:%M:%S')))
    print("Current local timestamp : " + str(int(time.time())) + ' - ' + str(time.strftime('%m/%d/%Y %H:%M:%S', time.localtime(int(time.time())))))



    if last_epoch_timestamp + int(args.secondstoprocess) < int(time.mktime(time.strptime(datetime.datetime.utcnow().strftime('%Y-%m-%d %H.%M.%S'), '%Y-%m-%d %H.%M.%S'))):
        start_epoch_timestamp = last_epoch_timestamp
        end_epoch_timestamp = last_epoch_timestamp + int(args.secondstoprocess)
        print('Processing start_epoch_timestamp in milliseconds: ' + str(last_epoch_timestamp*1000) + ' - ' + str(datetime.datetime.fromtimestamp(last_epoch_timestamp).strftime('%m/%d/%Y %H:%M:%S')))
        print('Processing end_epoch_timestamp in milliseconds : ' + str(end_epoch_timestamp*1000)  + ' - ' + str(datetime.datetime.fromtimestamp(end_epoch_timestamp).strftime('%m/%d/%Y %H:%M:%S')))


        try:
            next_token = "initialize"
            limit = 10
            countLogGroups = 1
            resultMessage = ""
            
            #while "nextToken" in logGroups:
            while next_token:
                
                if str(next_token) != "None":
                    prev_token = next_token
                if next_token == "initialize":
                    logGroups = logsclient.describe_log_groups(limit=limit)
                else:
                    logGroups = logsclient.describe_log_groups(
                        nextToken=next_token, limit=limit)
                
                next_token = logGroups.get("nextToken")

                if str(next_token) == "None" and limit != 1:
                    next_token = prev_token
                    limit = 1
                    logGroups = logsclient.describe_log_groups(
                        nextToken=next_token, limit=limit)
                    next_token = logGroups.get("nextToken")

                #Iterate through log groups
                for logGroup in logGroups["logGroups"]:
                    print("%s: %s: %s" %
                        (time.strftime('%Y-%d-%m %H:%M:%S'), countLogGroups, logGroup["logGroupName"]))
                    countLogGroups = countLogGroups + 1
                    resultMessage += "%s: %s\n" % (time.strftime('%Y-%d-%m %H:%M:%S'),
                                                logGroup["logGroupName"])
                    exportTaskResponse = logsclient.create_export_task(
                        taskName=logGroup["logGroupName"],
                        logGroupName=logGroup["logGroupName"],
                        fromTime=start_epoch_timestamp*1000,
                        to=end_epoch_timestamp*1000,
                        destination=args.destinationbucket,
                        destinationPrefix='ana%s/%s/%s/%s/%s/%s' % (logGroup["logGroupName"]                                        
                                            , time.strftime('%Y', time.localtime(end_epoch_timestamp))                                       
                                            , time.strftime('%m', time.localtime(end_epoch_timestamp))           
                                            , time.strftime('%d', time.localtime(end_epoch_timestamp))                                                    
                                            , time.strftime('%H', time.localtime(end_epoch_timestamp))                                        
                                            , time.strftime('%M', time.localtime(end_epoch_timestamp))                                       
                                            )
                    )

                    #There is a limit of 1 export running at a time so wait until current export is finshed
                    exportStatus = logsclient.describe_export_tasks(
                        taskId=exportTaskResponse["taskId"])
                    print(exportStatus["exportTasks"][0]["status"]["code"])
                    resultMessage += exportStatus["exportTasks"][0]["status"]["code"] + '\n'
                    while exportStatus["exportTasks"][0]["status"]["code"] != "COMPLETED":
                        exportStatus = logsclient.describe_export_tasks(
                            taskId=exportTaskResponse["taskId"])
                        time.sleep(10)
                        print(exportStatus["exportTasks"][0]["status"]["code"])
                        resultMessage += exportStatus["exportTasks"][0]["status"]["code"] + '\n'


        except ClientError as e:
            print(e.response)

        insert_table(args.ddb, args.ddbstream,
                     end_epoch_timestamp, args.region)

        elapsed_time = time.time() - start_time
        print(time.strftime("%H:%M:%S", time.gmtime(elapsed_time)))
    else:
        print("Not exporting logs as difference between current timestamp and timestamp returned from dynamodb is less than " + args.secondstoprocess + ' seconds')

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
