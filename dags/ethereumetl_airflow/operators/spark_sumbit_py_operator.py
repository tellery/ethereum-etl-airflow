import os

from airflow.models import BaseOperator
from airflow.settings import WEB_COLORS
from airflow.utils.decorators import apply_defaults
from ethereumetl_airflow.operators.fixed_spark_submit_hook import FixedSparkSubmitHook


class SparkSubmitPyOperator(BaseOperator):
    """
    It copies from SparkSubmitOperator
    https://github.com/apache/airflow/blob/v1-10-stable/airflow/contrib/operators/spark_submit_operator.py

    Overwrite it with the FixedSparkSubmitHook, and wrapping the process that render the spark file.
    """
    template_fields = ('_conf', '_files', '_driver_class_path', '_packages', '_exclude_packages', '_keytab',
                       '_principal', '_proxy_user', '_name', '_application_args', '_env_vars')
    ui_color = WEB_COLORS['LIGHTORANGE']

    @apply_defaults
    def __init__(self,
                 table_name,
                 dataset_name,
                 render_context,
                 # About Spark
                 conf=None,
                 conn_id='spark_default',
                 files=None,
                 archives=None,
                 driver_class_path=None,
                 packages=None,
                 exclude_packages=None,
                 repositories=None,
                 total_executor_cores=None,
                 executor_cores=None,
                 executor_memory=None,
                 driver_memory=None,
                 keytab=None,
                 principal=None,
                 proxy_user=None,
                 name='airflow-spark',
                 num_executors=None,
                 status_poll_interval=1,
                 application_args=None,
                 env_vars=None,
                 verbose=False,
                 spark_binary=None,
                 jars=None,
                 py_files=None,
                 *args,
                 **kwargs):
        super(SparkSubmitPyOperator, self).__init__(
            task_id=f'{dataset_name}.{table_name}',
            name=f'{dataset_name}_{table_name}',
            *args, **kwargs
        )

        self._table_name = table_name
        self._dataset_name = dataset_name
        self._render_context = render_context

        # About Spark
        self._conf = conf
        self._files = files
        self._archives = archives
        self._driver_class_path = driver_class_path
        self._packages = packages
        self._exclude_packages = exclude_packages
        self._repositories = repositories
        self._total_executor_cores = total_executor_cores
        self._executor_cores = executor_cores
        self._executor_memory = executor_memory
        self._driver_memory = driver_memory
        self._keytab = keytab
        self._principal = principal
        self._proxy_user = proxy_user
        self._name = name
        self._num_executors = num_executors
        self._status_poll_interval = status_poll_interval
        self._application_args = application_args
        self._env_vars = env_vars
        self._verbose = verbose
        self._spark_binary = spark_binary
        self._hook = None
        self._conn_id = conn_id
        self._jars = jars
        self._py_files = py_files

    def _render_pyspark(self, context):
        pyspark_path = os.path.join('/tmp', '{dataset}_{table}_{ds}.py'.format(
            dataset=self._dataset_name,
            table=self._table_name,
            ds=context['ds']
        ))
        dags_folder = os.environ.get('DAGS_FOLDER', '/opt/airflow/dags/repo/dags')
        pyspark_template_path = os.path.join(dags_folder, 'resources/stages/parse/spark/parse_contract.py')
        pyspark_template = self.read_file(pyspark_template_path)
        pyspark = self.render_template(pyspark_template, {
            'database': self._dataset_name,
            'table': self._table_name,
            'ds': context['ds'],
            **self._render_context
        })

        print('Load pyspark:')
        print(pyspark)

        with open(pyspark_path, 'w') as f:
            f.write(pyspark)

        return 'file://' + pyspark_path

    def execute(self, context):
        pyspark_path = self._render_pyspark(context)

        """
            Call the SparkSubmitHook to run the provided spark job
        """
        self._hook = FixedSparkSubmitHook(
            conf=self._conf,
            conn_id=self._conn_id,
            files=self._files,
            archives=self._archives,
            driver_class_path=self._driver_class_path,
            packages=self._packages,
            exclude_packages=self._exclude_packages,
            repositories=self._repositories,
            total_executor_cores=self._total_executor_cores,
            executor_cores=self._executor_cores,
            executor_memory=self._executor_memory,
            driver_memory=self._driver_memory,
            keytab=self._keytab,
            principal=self._principal,
            proxy_user=self._proxy_user,
            name=self._name,
            num_executors=self._num_executors,
            status_poll_interval=self._status_poll_interval,
            application_args=self._application_args,
            env_vars=self._env_vars,
            verbose=self._verbose,
            spark_binary=self._spark_binary,
            jars=self._jars,
            py_files=self._py_files
        )
        self._hook.submit(pyspark_path)

        """
            Clean temp environment
        """
        if os.path.isfile(pyspark_path):
            os.remove(pyspark_path)

    def on_kill(self):
        self._hook.on_kill()

    @staticmethod
    def read_file(filepath):
        with open(filepath) as file_handle:
            content = file_handle.read()
            return content
