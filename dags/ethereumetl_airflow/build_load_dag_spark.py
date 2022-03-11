import logging
import os
from datetime import datetime, timedelta

from airflow import models
from airflow.contrib.operators.s3_delete_objects_operator import S3DeleteObjectsOperator
from airflow.sensors.s3_key_sensor import S3KeySensor
from ethereumetl_airflow.operators.spark_submit_clean_operator import SparkSubmitCleanOperator
from ethereumetl_airflow.operators.spark_submit_enrich_operator import SparkSubmitEnrichOperator
from ethereumetl_airflow.operators.spark_submit_load_operator import SparkSubmitLoadOperator

logging.basicConfig()
logging.getLogger().setLevel(logging.DEBUG)


def build_load_dag_spark(
        dag_id,
        output_bucket,
        chain='ethereum',
        notification_emails=None,
        spark_conf=None,
        load_start_date=datetime(2018, 7, 1),
        schedule_interval='0 0 * * *',
):
    # The following datasets must be created in Spark:
    # - crypto_{chain}_raw
    # - crypto_{chain}_temp
    # - crypto_{chain}

    dataset_name = f'{chain}'
    dataset_name_temp = f'{chain}_raw'

    if not spark_conf:
        raise ValueError('k8s_config is required')

    # TODO: the retries number and retry delay for test
    default_dag_args = {
        'depends_on_past': False,
        'start_date': load_start_date,
        'email_on_failure': True,
        'email_on_retry': False,
        'retries': 2,
        'retry_delay': timedelta(minutes=1)
    }

    if notification_emails and len(notification_emails) > 0:
        default_dag_args['email'] = [email.strip() for email in notification_emails.split(',')]

    # Define a DAG (directed acyclic graph) of tasks.
    dag = models.DAG(
        dag_id,
        catchup=False,
        schedule_interval=schedule_interval,
        default_args=default_dag_args
    )

    dags_folder = os.environ.get('DAGS_FOLDER', '/opt/airflow/dags/repo/dags')

    def add_load_tasks(task, file_format):
        bucket_file_key = 'export/{task}/block_date={datestamp}/{task}.{file_format}'.format(
            task=task, datestamp='{{ds}}', file_format=file_format
        )

        wait_sensor = S3KeySensor(
            task_id='wait_latest_{task}'.format(task=task),
            timeout=60 * 60,
            poke_interval=60,
            bucket_key=bucket_file_key,
            bucket_name=output_bucket,
            dag=dag
        )

        load_operator = SparkSubmitLoadOperator(
            task_id='load_{task}'.format(task=task),
            dag=dag,
            name='load_{task}'.format(task=task),
            conf=spark_conf,
            template_conf={
                'task': task,
                'bucket': output_bucket,
                'database': dataset_name_temp,
                'operator_type': 'load',
                'file_format': file_format,
                'sql_template_path': os.path.join(
                    dags_folder,
                    'resources/stages/raw/sqls_spark/{task}.sql'.format(task=task)),
                'pyspark_template_path': os.path.join(
                    dags_folder,
                    'resources/stages/spark/spark_sql.py.template')
            }
        )

        wait_sensor >> load_operator
        return load_operator

    def add_enrich_tasks(task,
                         write_mode='overwrite',
                         sql_template_path='resources/stages/enrich/sqls/spark/{task}.sql',
                         pyspark_template_path='resources/stages/spark/insert_into_table.py.template',
                         dependencies=None):
        enrich_operator = SparkSubmitEnrichOperator(
            task_id='enrich_{task}'.format(task=task),
            dag=dag,
            name='enrich_{task}'.format(task=task),
            conf=spark_conf,
            template_conf={
                'task': task,
                'database': dataset_name,
                'write_mode': write_mode,
                'operator_type': 'enrich',
                'database_temp': dataset_name_temp,
                'sql_template_path': os.path.join(
                    dags_folder,
                    sql_template_path.format(task=task)),
                'pyspark_template_path': os.path.join(
                    dags_folder,
                    pyspark_template_path)
            }
        )

        if dependencies is not None and len(dependencies) > 0:
            for dependency in dependencies:
                dependency >> enrich_operator
        return enrich_operator

    def add_clean_tasks(task, file_format, dependencies=None):
        bucket_file_key = 'export/{task}/block_date={datestamp}/{task}.{file_format}'.format(
            task=task, datestamp='{{ds}}', file_format=file_format
        )

        s3_delete_operator = S3DeleteObjectsOperator(
            task_id='clean_{task}_s3_file'.format(task=task),
            dag=dag,
            bucket=output_bucket,
            keys=bucket_file_key,
        )

        clean_operator = SparkSubmitCleanOperator(
            task_id='clean_{task}'.format(task=task),
            dag=dag,
            name='clean_{task}'.format(task=task),
            conf=spark_conf,
            template_conf={
                'task': task,
                'operator_type': 'clean',
                'database_temp': dataset_name_temp,
                'sql_template_path': os.path.join(
                    dags_folder,
                    'resources/stages/enrich/sqls/spark/clean_table.sql'),
                'pyspark_template_path': os.path.join(
                    dags_folder,
                    'resources/stages/spark/spark_sql.py.template')
            }
        )

        # Drop table firstly and then delete data in S3.
        if dependencies is not None and len(dependencies) > 0:
            for dependency in dependencies:
                dependency >> clean_operator

        clean_operator >> s3_delete_operator

    # Load tasks #
    load_blocks_task = add_load_tasks('blocks', 'json')
    load_transactions_task = add_load_tasks('transactions', 'json')
    load_receipts_task = add_load_tasks('receipts', 'json')
    load_logs_task = add_load_tasks('logs', 'json')
    load_token_transfers_task = add_load_tasks('token_transfers', 'json')
    load_traces_task = add_load_tasks('traces', 'json')
    load_contracts_task = add_load_tasks('contracts', 'json')
    load_tokens_task = add_load_tasks('tokens', 'json')

    # Enrich tasks #
    enrich_blocks_task = add_enrich_tasks(
        'blocks', 'overwrite', dependencies=[load_blocks_task])
    enrich_transactions_task = add_enrich_tasks(
        'transactions', 'overwrite', dependencies=[load_blocks_task, load_transactions_task, load_receipts_task])
    enrich_logs_task = add_enrich_tasks(
        'logs', 'append',
        sql_template_path="resources/stages/enrich/sqls/spark_optimize/{task}.sql",
        pyspark_template_path="resources/stages/spark/append_to_partitioned_table.py.template",
        dependencies=[load_blocks_task, load_logs_task])
    enrich_token_transfers_task = add_enrich_tasks(
        'token_transfers', 'overwrite', dependencies=[load_blocks_task, load_token_transfers_task])
    enrich_traces_task = add_enrich_tasks(
        'traces', 'append',
        sql_template_path="resources/stages/enrich/sqls/spark_optimize/{task}.sql",
        pyspark_template_path="resources/stages/spark/append_to_partitioned_table.py.template",
        dependencies=[load_blocks_task, load_traces_task])
    enrich_contracts_task = add_enrich_tasks(
        'contracts', 'overwrite', dependencies=[load_blocks_task, load_contracts_task])
    enrich_tokens_task = add_enrich_tasks(
        'tokens', 'append', dependencies=[load_tokens_task])

    # Clean tasks #
    add_clean_tasks('blocks', 'json', dependencies=[
        enrich_blocks_task,
        enrich_transactions_task,
        enrich_logs_task,
        enrich_token_transfers_task,
        enrich_traces_task,
        enrich_contracts_task
    ])
    add_clean_tasks('transactions', 'json', dependencies=[enrich_transactions_task])
    add_clean_tasks('logs', 'json', dependencies=[enrich_logs_task])
    add_clean_tasks('token_transfers', 'json', dependencies=[enrich_token_transfers_task])
    add_clean_tasks('traces', 'json', dependencies=[enrich_traces_task])
    add_clean_tasks('contracts', 'json', dependencies=[enrich_contracts_task])
    add_clean_tasks('tokens', 'json', dependencies=[enrich_tokens_task])
    add_clean_tasks('receipts', 'json', dependencies=[enrich_transactions_task])

    return dag
