<?php
/**
 * WooCommerce > InvenTree Shop settings page.
 *
 * @package InvenTreeShop
 */

defined( 'ABSPATH' ) || exit;

class InvenTree_Shop_Settings {

	const FIELDS = array(
		'inventree_shop_inventree_url'  => 'url',
		'inventree_shop_secret'         => 'secret',
		'inventree_shop_quote_page'     => 'page',
		'inventree_shop_notify_email'   => 'email',
		'inventree_shop_phone'          => 'text',
		'inventree_shop_sample_address' => 'textarea',
		'inventree_shop_custom_button'  => 'checkbox',
		'inventree_shop_max_files'      => 'number',
		'inventree_shop_max_file_mb'    => 'number',
	);

	public static function init() {
		add_action( 'admin_menu', array( __CLASS__, 'menu' ), 60 );
		add_action( 'admin_init', array( __CLASS__, 'register' ) );
	}

	public static function get( $key, $default = '' ) {
		$value = get_option( 'inventree_shop_' . $key, $default );
		return ( '' === $value || null === $value ) ? $default : $value;
	}

	public static function quote_page_url( $args = array() ) {
		$page = (int) self::get( 'quote_page', 0 );
		$url  = $page ? get_permalink( $page ) : '';
		return $url ? add_query_arg( $args, $url ) : '';
	}

	public static function menu() {
		add_submenu_page( 'woocommerce', __( 'InvenTree Shop', 'inventree-shop' ), __( 'InvenTree Shop', 'inventree-shop' ), 'manage_woocommerce', 'inventree-shop', array( __CLASS__, 'render' ) );
	}

	public static function register() {
		foreach ( self::FIELDS as $name => $type ) {
			register_setting(
				'inventree_shop',
				$name,
				array(
					'sanitize_callback' => function ( $value ) use ( $type, $name ) {
						switch ( $type ) {
							case 'url':
								$value = esc_url_raw( trim( (string) $value ) );
								return InvenTree_Link::allowed_url( $value ) ? untrailingslashit( $value ) : '';
							case 'secret':
								$value = trim( (string) $value );
								return '' === $value ? get_option( $name, '' ) : $value; // blank = keep the saved one
							case 'page':
							case 'number':
								return absint( $value );
							case 'email':
								return sanitize_email( $value );
							case 'textarea':
								return sanitize_textarea_field( $value );
							case 'checkbox':
								return $value ? 'yes' : 'no';
							default:
								return sanitize_text_field( $value );
						}
					},
				)
			);
		}
	}

	public static function render() {
		if ( ! current_user_can( 'manage_woocommerce' ) ) {
			return;
		}
		$secret_set = '' !== get_option( 'inventree_shop_secret', '' );
		?>
		<div class="wrap">
			<h1><?php esc_html_e( 'InvenTree Shop', 'inventree-shop' ); ?></h1>
			<p><?php esc_html_e( 'Connects quote requests and account trading to InvenTree (inventree-woocommerce plugin).', 'inventree-shop' ); ?></p>
			<form method="post" action="options.php">
				<?php settings_fields( 'inventree_shop' ); ?>
				<table class="form-table" role="presentation">
					<tr>
						<th><label for="inventree_shop_inventree_url"><?php esc_html_e( 'InvenTree address', 'inventree-shop' ); ?></label></th>
						<td><input type="url" class="regular-text" id="inventree_shop_inventree_url" name="inventree_shop_inventree_url" value="<?php echo esc_attr( get_option( 'inventree_shop_inventree_url', '' ) ); ?>" placeholder="https://inventree.example.com">
							<p class="description"><?php esc_html_e( 'https only.', 'inventree-shop' ); ?></p></td>
					</tr>
					<tr>
						<th><label for="inventree_shop_secret"><?php esc_html_e( 'Shop link secret', 'inventree-shop' ); ?></label></th>
						<td><input type="password" class="regular-text" id="inventree_shop_secret" name="inventree_shop_secret" value="" autocomplete="new-password" placeholder="<?php echo $secret_set ? esc_attr__( '(saved - leave blank to keep)', 'inventree-shop' ) : ''; ?>">
							<p class="description"><?php esc_html_e( 'The same value as "Shop link secret" in the InvenTree plugin settings.', 'inventree-shop' ); ?></p></td>
					</tr>
					<tr>
						<th><label for="inventree_shop_quote_page"><?php esc_html_e( 'Quote request page', 'inventree-shop' ); ?></label></th>
						<td><?php wp_dropdown_pages( array( 'name' => 'inventree_shop_quote_page', 'id' => 'inventree_shop_quote_page', 'selected' => (int) get_option( 'inventree_shop_quote_page', 0 ), 'show_option_none' => __( '- choose -', 'inventree-shop' ) ) ); ?>
							<p class="description"><?php esc_html_e( 'A page containing the [inventree_quote_request] shortcode.', 'inventree-shop' ); ?></p></td>
					</tr>
					<tr>
						<th><label for="inventree_shop_notify_email"><?php esc_html_e( 'Send new quote requests to', 'inventree-shop' ); ?></label></th>
						<td><input type="email" class="regular-text" id="inventree_shop_notify_email" name="inventree_shop_notify_email" value="<?php echo esc_attr( get_option( 'inventree_shop_notify_email', '' ) ); ?>" placeholder="<?php echo esc_attr( get_option( 'admin_email' ) ); ?>"></td>
					</tr>
					<tr>
						<th><label for="inventree_shop_phone"><?php esc_html_e( 'Phone for customers', 'inventree-shop' ); ?></label></th>
						<td><input type="text" class="regular-text" id="inventree_shop_phone" name="inventree_shop_phone" value="<?php echo esc_attr( get_option( 'inventree_shop_phone', '' ) ); ?>"></td>
					</tr>
					<tr>
						<th><label for="inventree_shop_sample_address"><?php esc_html_e( 'Send samples to', 'inventree-shop' ); ?></label></th>
						<td><textarea class="large-text" rows="4" id="inventree_shop_sample_address" name="inventree_shop_sample_address"><?php echo esc_textarea( get_option( 'inventree_shop_sample_address', '' ) ); ?></textarea>
							<p class="description"><?php esc_html_e( 'Printed on the sample label customers attach to their parcel.', 'inventree-shop' ); ?></p></td>
					</tr>
					<tr>
						<th><?php esc_html_e( 'Product pages', 'inventree-shop' ); ?></th>
						<td><label><input type="checkbox" name="inventree_shop_custom_button" value="1" <?php checked( 'no' !== get_option( 'inventree_shop_custom_button', 'yes' ) ); ?>> <?php esc_html_e( 'Show a "Request a custom version" button', 'inventree-shop' ); ?></label></td>
					</tr>
					<tr>
						<th><?php esc_html_e( 'Quote uploads', 'inventree-shop' ); ?></th>
						<td>
							<label><?php esc_html_e( 'Most files', 'inventree-shop' ); ?> <input type="number" min="1" class="small-text" name="inventree_shop_max_files" value="<?php echo esc_attr( get_option( 'inventree_shop_max_files', 20 ) ); ?>"></label>
							<label><?php esc_html_e( 'Largest file (MB)', 'inventree-shop' ); ?> <input type="number" min="1" class="small-text" name="inventree_shop_max_file_mb" value="<?php echo esc_attr( get_option( 'inventree_shop_max_file_mb', 20 ) ); ?>"></label>
							<p class="description">
								<?php
								/* translators: 1: upload_max_filesize, 2: post_max_size */
								echo esc_html( sprintf( __( 'Keep these the same as the InvenTree plugin settings. This server allows %1$s per file and %2$s per request (PHP upload_max_filesize / post_max_size).', 'inventree-shop' ), ini_get( 'upload_max_filesize' ), ini_get( 'post_max_size' ) ) );
								?>
							</p>
						</td>
					</tr>
				</table>
				<?php submit_button(); ?>
			</form>
		</div>
		<?php
	}
}
